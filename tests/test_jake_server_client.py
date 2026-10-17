"""F9.1: la sonda di Jake Server dal PC (core/jake_server_client.py) contro server veri su 127.0.0.1: un Jake Server
sano, una porta chiusa, un socket che non risponde mai, server che rispondono in modo sbagliato. Nessun mock di rete:
il punto e' proprio che errori veri del sistema operativo non arrivino mai al runtime come eccezioni."""
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

from core.config import Config
from core.jake_server_client import JakeServerClient, ServerState
from jake_server.health import HealthServer

SHORT_TIMEOUT = 0.3


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _CannedServer:
    """Server HTTP che risponde sempre con lo stesso status e lo stesso corpo."""

    def __init__(self, status: int, body: bytes):
        canned = (status, body)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                code, payload = canned
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
