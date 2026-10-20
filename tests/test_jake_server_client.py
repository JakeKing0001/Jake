"""F9.1/F9.2: la sonda di Jake Server dal PC (core/jake_server_client.py) contro server veri su 127.0.0.1: un Jake
Server sano, una porta chiusa, un socket che non risponde mai, server che rispondono in modo sbagliato o con un
protocollo incompatibile. Il punto e' che errori veri del sistema operativo non arrivino mai al runtime come eccezioni;
solo i test di cache/backoff usano un opener finto, per contare le richieste con un orologio finto."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib import error as urllib_error

from core.companion_guard import MIN_PROTOCOL_VERSION
from core.config import Config
from core.jake_server_client import JakeServerClient, ServerState, protocol_compatible
from core.version import PROTOCOL_VERSION
from jake_server.health import HealthServer

SHORT_TIMEOUT = 0.3


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _CannedServer:
    """Server HTTP che risponde sempre con lo stesso status e lo stesso corpo, salvo i percorsi in `routes`."""

    def __init__(self, status: int, body: bytes, routes: dict | None = None):
        canned = (status, body)
        by_path = dict(routes or {})

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                code, payload = by_path.get(self.path, canned)
                self.send_response(code)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _RawReplyServer:
    """Accetta una connessione e risponde con byte che non sono HTTP."""

    def __init__(self, reply: bytes):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self._serve, args=(reply,), daemon=True).start()

    def _serve(self, reply: bytes):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.recv(4096)
            conn.sendall(reply)

    def close(self):
        self.sock.close()


class _SilentServer:
    """Accetta la connessione TCP (backlog del kernel) e non risponde mai: simula un server appeso."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def close(self):
        self.sock.close()


class OnlineTests(unittest.TestCase):
    def test_a_real_jake_server_is_online(self):
        server = HealthServer(host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        client = JakeServerClient(f"http://127.0.0.1:{server.port}/", timeout=2)

        status = client.probe()

        self.assertEqual(status.state, ServerState.ONLINE)
        self.assertEqual(status.reason, "ok")
        self.assertEqual(status.version, server.payload()["version"])
        self.assertIsNotNone(status.latency_ms)
        self.assertTrue(client.is_available)
        self.assertEqual(client.last_status, status)


class OfflineTests(unittest.TestCase):
    def test_closed_port_is_offline(self):
        client = JakeServerClient(f"http://127.0.0.1:{_free_port()}", timeout=SHORT_TIMEOUT)

        status = client.probe()

        self.assertEqual(status.state, ServerState.OFFLINE)
        self.assertIn(status.reason, {"refused", "timeout", "unreachable"})
        self.assertFalse(client.is_available)

    def test_unresolvable_host_is_offline(self):
        # .invalid non si risolve mai per definizione (RFC 2606).
        status = JakeServerClient("http://jake-server.invalid:8770", timeout=SHORT_TIMEOUT).probe()

        self.assertEqual(status.state, ServerState.OFFLINE)
        self.assertIn(status.reason, {"unresolved_host", "unreachable", "timeout"})

    def test_server_that_never_answers_times_out_quickly(self):
        silent = _SilentServer()
        self.addCleanup(silent.close)
        client = JakeServerClient(silent.url, timeout=SHORT_TIMEOUT)

        started = time.monotonic()
        status = client.probe()
        elapsed = time.monotonic() - started

        self.assertEqual(status.state, ServerState.OFFLINE)
        self.assertEqual(status.reason, "timeout")
        self.assertLess(elapsed, SHORT_TIMEOUT + 1.0)


class DegradedTests(unittest.TestCase):
    def _probe_canned(self, status: int, body: bytes):
        server = _CannedServer(status, body)
        self.addCleanup(server.close)
        return JakeServerClient(server.url, timeout=2).probe()

    def test_non_json_body_is_malformed(self):
        status = self._probe_canned(200, b"<html>router login</html>")
        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "malformed_response"))

    def test_json_that_is_not_an_object_is_malformed(self):
        status = self._probe_canned(200, b'["ok"]')
        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "malformed_response"))

    def test_oversized_body_is_malformed(self):
        status = self._probe_canned(200, b'{"service": "jake-server", "pad": "' + b"x" * 20000 + b'"}')
        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "malformed_response"))

    def test_another_service_on_that_port_is_unexpected(self):
        status = self._probe_canned(200, json.dumps({"status": "ok", "service": "something-else"}).encode())
        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "unexpected_service"))

    def test_jake_server_reporting_trouble_is_unhealthy(self):
        body = json.dumps({"status": "starting", "service": "jake-server", "version": "5.9"}).encode()
        status = self._probe_canned(200, body)
        self.assertEqual((status.state, status.reason, status.version), (ServerState.DEGRADED, "unhealthy", "5.9"))

    def test_http_error_status_is_degraded(self):
        status = self._probe_canned(503, b'{"error": "busy"}')
        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "http_503"))

    def test_redirect_is_not_followed(self):
        status = self._probe_canned(302, b"")
        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "http_302"))

    def test_bytes_that_are_not_http_are_malformed(self):
        raw = _RawReplyServer(b"SSH-2.0-OpenSSH_9.6\r\n")
        self.addCleanup(raw.close)
        status = JakeServerClient(raw.url, timeout=2).probe()
        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "malformed_response"))


_HEALTH_OK = json.dumps({"status": "ok", "service": "jake-server", "version": "5.9"}).encode()


def _described(**fields) -> bytes:
    payload = {"service": "jake-server", "node": "server", "version": "5.9", "protocol_version": PROTOCOL_VERSION,
               "min_protocol_version": MIN_PROTOCOL_VERSION, "capabilities": ["health"]}
    payload.update(fields)
    return json.dumps(payload).encode()


class CapabilityTests(unittest.TestCase):
    """F9.2: oltre a "e' vivo", il client sa con quale protocollo parla il nodo e cosa offre."""

    def _probe_routes(self, capabilities: tuple[int, bytes]):
        server = _CannedServer(200, _HEALTH_OK, routes={"/capabilities": capabilities})
        self.addCleanup(server.close)
        client = JakeServerClient(server.url, timeout=2)
        return client, client.probe()

    def test_online_status_carries_version_protocol_capabilities_and_last_success(self):
        server = HealthServer(host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        client = JakeServerClient(f"http://127.0.0.1:{server.port}", timeout=2)

        status = client.probe()

        self.assertEqual(status.state, ServerState.ONLINE)
        self.assertEqual(status.protocol_version, PROTOCOL_VERSION)
        self.assertEqual(status.capabilities, ("health",))
        self.assertEqual(status.last_success_at, status.checked_at)
        self.assertTrue(client.supports("health"))
        self.assertFalse(client.supports("memory"))

    def test_a_server_from_f9_1_without_capabilities_is_degraded_not_offline(self):
        client, status = self._probe_routes((404, b'{"error": "not_found"}'))

        self.assertEqual((status.state, status.reason, status.version),
                         (ServerState.DEGRADED, "capabilities_unavailable", "5.9"))
        self.assertFalse(client.supports("health"))

    def test_incompatible_protocol_is_degraded_and_offers_nothing(self):
        newer = PROTOCOL_VERSION + 5
        client, status = self._probe_routes((200, _described(protocol_version=newer, min_protocol_version=newer)))

        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "protocol_incompatible"))
        self.assertEqual(status.protocol_version, newer)
        self.assertEqual(status.capabilities, ("health",))
        self.assertFalse(client.is_available)
        self.assertFalse(client.supports("health"))

    def test_overlapping_protocol_windows_are_compatible(self):
        _, status = self._probe_routes((200, _described(protocol_version=PROTOCOL_VERSION + 1,
                                                        min_protocol_version=PROTOCOL_VERSION)))

        self.assertEqual(status.state, ServerState.ONLINE)
        self.assertEqual(status.protocol_version, PROTOCOL_VERSION + 1)

    def test_compatibility_is_the_overlap_of_the_two_windows(self):
        self.assertTrue(protocol_compatible(PROTOCOL_VERSION, PROTOCOL_VERSION))
        self.assertTrue(protocol_compatible(PROTOCOL_VERSION + 3, MIN_PROTOCOL_VERSION))
        self.assertFalse(protocol_compatible(PROTOCOL_VERSION + 3, PROTOCOL_VERSION + 1))
        self.assertFalse(protocol_compatible(MIN_PROTOCOL_VERSION - 1, MIN_PROTOCOL_VERSION - 1))

    def test_malformed_capability_descriptions_are_degraded(self):
        cases = {
            "not json": b"capabilities: health",
            "capabilities not a list": _described(capabilities="health"),
            "unsafe name": _described(capabilities=["../etc/passwd"]),
            "upper case name": _described(capabilities=["Health"]),
            "too many": _described(capabilities=[f"cap{i}" for i in range(40)]),
            "protocol not an int": _described(protocol_version="1"),
            "protocol is a bool": _described(protocol_version=True),
            "min above max": _described(protocol_version=1, min_protocol_version=2),
        }
        for name, body in cases.items():
            with self.subTest(name):
                _, status = self._probe_routes((200, body))
                self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "malformed_response"))

    def test_capabilities_from_another_service_are_unexpected(self):
        _, status = self._probe_routes((200, _described(service="something-else")))

        self.assertEqual((status.state, status.reason), (ServerState.DEGRADED, "unexpected_service"))

    def test_duplicate_capabilities_are_kept_once(self):
        _, status = self._probe_routes((200, _described(capabilities=["health", "health"])))

        self.assertEqual(status.capabilities, ("health",))


class RecoveryTests(unittest.TestCase):
    def test_server_that_comes_back_turns_online_and_remembers_the_last_success(self):
        port = _free_port()
        client = JakeServerClient(f"http://127.0.0.1:{port}", timeout=SHORT_TIMEOUT)

        first = client.probe()
        server = HealthServer(host="127.0.0.1", port=port)
        server.start()
        back = client.probe()
        server.stop()
        gone_again = client.probe()

        self.assertEqual(first.state, ServerState.OFFLINE)
        self.assertIsNone(first.last_success_at)
        self.assertEqual(back.state, ServerState.ONLINE)
        self.assertEqual(gone_again.state, ServerState.OFFLINE)
        self.assertEqual(gone_again.last_success_at, back.checked_at)
        self.assertFalse(client.is_available)


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _CountingOpener:
    """Opener che conta le richieste e risponde "connessione rifiutata" (o si blocca finche' `release` non e' set)."""

    def __init__(self, block: threading.Event | None = None):
        self.calls = 0
        self.block = block

    def open(self, req, timeout=None):
        self.calls += 1
        if self.block is not None:
            self.block.wait(5)
        raise urllib_error.URLError(ConnectionRefusedError())


class CacheAndBackoffTests(unittest.TestCase):
    """F9.2: niente polling aggressivo - una sonda solo quando la precedente e' scaduta, mai due insieme."""

    def _refresh_and_wait(self, client) -> bool:
        thread = client.refresh_if_due()
        if thread is None:
            return False
        thread.join(5)
        return True

    def test_offline_server_is_retried_with_doubling_waits_up_to_the_cap(self):
        clock, opener = _FakeClock(), _CountingOpener()
        client = JakeServerClient("http://127.0.0.1:1", opener=opener, clock=clock)
        client.probe()  # primo fallimento
        waits = []
        for _ in range(7):
            waited = 0.0
            while not self._refresh_and_wait(client):
                clock.now += 1
                waited += 1
                self.assertLess(waited, 1000, "la sonda non e' mai tornata dovuta")
            waits.append(waited)

        self.assertEqual(waits, [15, 30, 60, 120, 240, 300, 300])
        self.assertEqual(opener.calls, 8)

    def test_online_server_is_rechecked_only_after_the_cache_expires(self):
        server = HealthServer(host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        clock = _FakeClock()
        client = JakeServerClient(f"http://127.0.0.1:{server.port}", timeout=2, clock=clock)
        self.assertTrue(self._refresh_and_wait(client))  # mai controllato: dovuta subito
        self.assertTrue(client.is_available)

        clock.now += 59
        self.assertIsNone(client.refresh_if_due())
        self.assertEqual(client.seconds_until_next_probe(), 1.0)
        clock.now += 1
        self.assertTrue(self._refresh_and_wait(client))

    def test_success_resets_the_backoff(self):
        port = _free_port()
        clock = _FakeClock()
        client = JakeServerClient(f"http://127.0.0.1:{port}", timeout=SHORT_TIMEOUT, clock=clock)
        for _ in range(3):
            client.probe()
        self.assertEqual(client.seconds_until_next_probe(), 60.0)  # 15 -> 30 -> 60
        server = HealthServer(host="127.0.0.1", port=port)
        server.start()
        self.addCleanup(server.stop)
        client.probe()
        server.stop()
        client.probe()

        self.assertEqual(client.seconds_until_next_probe(), 15.0)

    def test_only_one_probe_at_a_time(self):
        release = threading.Event()
        opener = _CountingOpener(block=release)
        client = JakeServerClient("http://127.0.0.1:1", opener=opener)

        first = client.refresh_if_due()
        second = client.refresh_if_due()
        third = client.probe_in_background()
        release.set()
        first.join(5)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNone(third)
        self.assertEqual(opener.calls, 1)

    def test_refresh_returns_at_once_even_if_the_server_hangs(self):
        silent = _SilentServer()
        self.addCleanup(silent.close)
        client = JakeServerClient(silent.url, timeout=SHORT_TIMEOUT)

        started = time.monotonic()
        thread = client.refresh_if_due()
        returned_after = time.monotonic() - started
        thread.join(5)

        self.assertLess(returned_after, 0.1)
        self.assertEqual(client.last_status.reason, "timeout")

    def test_disabled_client_never_schedules_anything(self):
        client = JakeServerClient(None)

        self.assertIsNone(client.refresh_if_due())
        self.assertIsNone(client.seconds_until_next_probe())


class ConfigurationTests(unittest.TestCase):
    def test_missing_url_is_disabled_and_starts_no_thread(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                client = JakeServerClient(value)
                self.assertIsNone(client.probe_in_background())
                self.assertEqual(client.last_status.state, ServerState.DISABLED)
                self.assertEqual(client.last_status.reason, "not_configured")

    def test_invalid_urls_are_disabled(self):
        for value in ("ftp://host:21", "host:8770", "http://user:pw@host:8770", "http://host:notaport",
                      "http://host:8770/?x=1"):
            with self.subTest(value=value):
                client = JakeServerClient(value)
                self.assertFalse(client.enabled)
                self.assertEqual(client.probe().reason, "invalid_url")

    def test_timeout_is_kept_short(self):
        self.assertEqual(JakeServerClient("http://h:1", timeout=60).timeout, 5.0)
        self.assertEqual(JakeServerClient("http://h:1", timeout=0).timeout, 0.2)
        self.assertEqual(JakeServerClient("http://h:1", timeout="boh").timeout, 1.5)

    def test_url_comes_from_jake_server_url_environment_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"JAKE_SERVER_URL": "http://10.0.0.5:8770",
                                              "JAKE_SERVER_TIMEOUT_SECONDS": "0.5"}):
                client = JakeServerClient.from_config(Config(path=Path(tmp) / "settings.json"))

        self.assertEqual(client.base_url, "http://10.0.0.5:8770")
        self.assertEqual(client.timeout, 0.5)


class NeverBlocksJakeTests(unittest.TestCase):
    def test_unexpected_failure_inside_the_probe_becomes_offline(self):
        class ExplodingOpener:
            def open(self, *args, **kwargs):
                raise RuntimeError("bug imprevisto")

        status = JakeServerClient("http://127.0.0.1:1", opener=ExplodingOpener()).probe()

        self.assertEqual((status.state, status.reason), (ServerState.OFFLINE, "error"))

    def test_background_probe_returns_at_once_even_if_the_server_hangs(self):
        silent = _SilentServer()
        self.addCleanup(silent.close)
        results = []

        def broken_callback(status):
            results.append(status)
            raise ValueError("una callback rotta non deve uscire dal thread")

        client = JakeServerClient(silent.url, timeout=SHORT_TIMEOUT)
        started = time.monotonic()
        thread = client.probe_in_background(broken_callback)
        returned_after = time.monotonic() - started
        thread.join(timeout=5)

        self.assertLess(returned_after, 0.1)
        self.assertTrue(thread.daemon)
        self.assertFalse(thread.is_alive())
        self.assertEqual([s.state for s in results], [ServerState.OFFLINE])
        self.assertEqual(client.last_status.reason, "timeout")


if __name__ == "__main__":
    unittest.main()
