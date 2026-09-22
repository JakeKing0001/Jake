"""F7.1.1, F7.1.4-F7.1.7: difese del server companion. Le regole (`core/companion_guard.py`) si provano con orologi
finti e numeri esatti; l'integrazione con un server VERO su una porta effimera, un vero DeviceCredentialStore
(DPAPI) e un vero handshake TLS con un certificato generato da `cryptography`."""
import datetime
import http.client
import ipaddress
import json
import shutil
import socket
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib import error, request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import core.companion_server as companion_server
from core.companion_guard import (
    DEFAULT_CAPABILITIES, MAX_BODY_BYTES, MIN_PROTOCOL_VERSION, AuthFailureThrottle, CompanionAudit, CompanionGuard,
    EndpointClass, InsecureBindError, Limit, RateLimiter, ReplayGuard, ValidationError, check_bind_policy,
    check_protocol, classify, is_loopback_host, text_fingerprint, validate_claim_body, validate_command_body,
    validate_identifier, validate_text,
)
from core.companion_server import CompanionServer
from core.device_credential_store import DeviceCredentialStore
from core.version import PROTOCOL_VERSION


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _call(base: str, method: str, path: str, payload=None, headers: dict | None = None, timeout: float = 5,
          context: ssl.SSLContext | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(base + path, data=data, method=method,
                          headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with request.urlopen(req, timeout=timeout, context=context) as response:
            return response.status, json.loads(response.read().decode("utf-8")), response.headers
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), exc.headers


def _raw(port: int, method: str, path: str, headers: dict, body: bytes = b"") -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest(method, path)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8") or "{}")
    finally:
        connection.close()


# ---- regole pure -------------------------------------------------------------------------------------------------------


class ClassificationTests(unittest.TestCase):
    def test_every_known_endpoint_has_its_own_class(self):
        self.assertEqual(classify("GET", "/status"), EndpointClass.READ_ONLY)
        self.assertEqual(classify("GET", "/events"), EndpointClass.READ_ONLY)
        self.assertEqual(classify("POST", "/command"), EndpointClass.COMMAND)
        self.assertEqual(classify("POST", "/devices/x/claim"), EndpointClass.COMMAND)
        self.assertEqual(classify("POST", "/devices/x/release"), EndpointClass.COMMAND)

    def test_reserved_prefixes_are_classified_before_the_endpoints_exist(self):
        self.assertEqual(classify("POST", "/approvals/abc/approve"), EndpointClass.APPROVAL)
        self.assertEqual(classify("POST", "/files/upload"), EndpointClass.FILE)
        self.assertEqual(classify("POST", "/audio/stream"), EndpointClass.AUDIO)

    def test_a_method_on_the_wrong_path_is_not_a_command(self):
        self.assertIsNone(classify("GET", "/command"))
        self.assertIsNone(classify("POST", "/status"))
        self.assertIsNone(classify("POST", "/unknown"))

    def test_query_strings_do_not_change_the_class(self):
        self.assertEqual(classify("GET", "/status?x=1"), EndpointClass.READ_ONLY)

    def test_default_capabilities_exclude_file_and_audio(self):
        self.assertNotIn(EndpointClass.FILE, DEFAULT_CAPABILITIES)
        self.assertNotIn(EndpointClass.AUDIO, DEFAULT_CAPABILITIES)


class CapabilityTests(unittest.TestCase):
    def test_an_unconfigured_device_gets_the_default_and_a_configured_one_only_what_it_was_given(self):
        guard = CompanionGuard()
        guard.set_capabilities("watch", [EndpointClass.READ_ONLY])
        self.assertTrue(guard.allowed("watch", EndpointClass.READ_ONLY))
        self.assertFalse(guard.allowed("watch", EndpointClass.COMMAND))
        self.assertTrue(guard.allowed("phone", EndpointClass.COMMAND))
        self.assertFalse(guard.allowed("phone", EndpointClass.FILE))
        self.assertTrue(guard.allowed(None, EndpointClass.COMMAND))

    def test_replay_is_required_by_default_only_off_loopback(self):
        guard = CompanionGuard()
        self.assertFalse(guard.replay_required("127.0.0.1"))
        self.assertFalse(guard.replay_required("localhost"))
        self.assertTrue(guard.replay_required("192.168.1.5"))
        self.assertTrue(CompanionGuard(require_replay_protection=True).replay_required("127.0.0.1"))
        self.assertFalse(CompanionGuard(require_replay_protection=False).replay_required("10.0.0.1"))


class BindPolicyTests(unittest.TestCase):
    def test_loopback_detection(self):
        for host in ("127.0.0.1", "127.0.0.2", "::1", "localhost", "LOCALHOST"):
            self.assertTrue(is_loopback_host(host), host)
        for host in ("0.0.0.0", "", "192.168.1.10", "10.0.0.2", "::", "jake.local"):
            self.assertFalse(is_loopback_host(host), host)

    def test_non_loopback_without_tls_is_forbidden_and_with_tls_allowed(self):
        for host in ("0.0.0.0", "192.168.1.10", ""):
            with self.assertRaises(InsecureBindError):
                check_bind_policy(host, has_tls=False)
            check_bind_policy(host, has_tls=True)
        check_bind_policy("127.0.0.1", has_tls=False)

    def test_start_refuses_before_opening_the_port(self):
        for host in ("0.0.0.0", "192.168.1.10"):
            server = CompanionServer(host=host, port=0)
            with self.assertRaises(InsecureBindError):
                server.start()
            self.assertFalse(server.running)


class RateLimiterTests(unittest.TestCase):
    def test_burst_then_refusal_then_exact_refill(self):
        clock = FakeClock()
        limiter = RateLimiter({EndpointClass.COMMAND: Limit(burst=3, per_second=1)}, clock=clock)
        for _ in range(3):
            self.assertTrue(limiter.allow("dev:a", EndpointClass.COMMAND)[0])
        ok, wait = limiter.allow("dev:a", EndpointClass.COMMAND)
        self.assertFalse(ok)
        self.assertAlmostEqual(wait, 1.0, places=6)
        clock.advance(0.5)
        ok, wait = limiter.allow("dev:a", EndpointClass.COMMAND)
        self.assertFalse(ok)
        self.assertAlmostEqual(wait, 0.5, places=6)
        clock.advance(0.5)
        self.assertTrue(limiter.allow("dev:a", EndpointClass.COMMAND)[0])
        self.assertFalse(limiter.allow("dev:a", EndpointClass.COMMAND)[0])

    def test_refill_never_exceeds_the_burst(self):
        clock = FakeClock()
        limiter = RateLimiter({EndpointClass.COMMAND: Limit(2, 1)}, clock=clock)
        clock.advance(10_000)
        self.assertTrue(limiter.allow("x", EndpointClass.COMMAND)[0])
        self.assertTrue(limiter.allow("x", EndpointClass.COMMAND)[0])
        self.assertFalse(limiter.allow("x", EndpointClass.COMMAND)[0])

    def test_identities_and_classes_are_independent(self):
        limiter = RateLimiter({EndpointClass.COMMAND: Limit(1, 0.001), EndpointClass.READ_ONLY: Limit(1, 0.001)}, clock=FakeClock())
        self.assertTrue(limiter.allow("dev:a", EndpointClass.COMMAND)[0])
        self.assertFalse(limiter.allow("dev:a", EndpointClass.COMMAND)[0])
        self.assertTrue(limiter.allow("dev:b", EndpointClass.COMMAND)[0])
        self.assertTrue(limiter.allow("dev:a", EndpointClass.READ_ONLY)[0])

    def test_a_flood_of_identities_does_not_grow_memory_without_bound(self):
        limiter = RateLimiter(clock=FakeClock(), max_keys=100)
        for index in range(1000):
            limiter.allow(f"ip:10.0.{index // 256}.{index % 256}", EndpointClass.READ_ONLY)
        self.assertLessEqual(len(limiter._buckets), 100)


class AuthFailureThrottleTests(unittest.TestCase):
    def test_locks_after_the_configured_failures_and_unlocks_on_time(self):
        clock = FakeClock()
        throttle = AuthFailureThrottle(max_failures=3, window=60, lockout=30, clock=clock)
        for _ in range(2):
            throttle.record_failure("ip:1")
        self.assertEqual(throttle.retry_after("ip:1"), 0.0)
        throttle.record_failure("ip:1")
        self.assertAlmostEqual(throttle.retry_after("ip:1"), 30.0, places=6)
        self.assertEqual(throttle.retry_after("ip:2"), 0.0)
        clock.advance(29)
        self.assertGreater(throttle.retry_after("ip:1"), 0.0)
        clock.advance(1.5)
        self.assertEqual(throttle.retry_after("ip:1"), 0.0)

    def test_failures_spread_beyond_the_window_never_lock(self):
        clock = FakeClock()
        throttle = AuthFailureThrottle(max_failures=3, window=10, lockout=30, clock=clock)
        for _ in range(10):
            throttle.record_failure("ip:1")
            clock.advance(6)
            self.assertEqual(throttle.retry_after("ip:1"), 0.0)


class ReplayGuardTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(10_000.0)
        self.guard = ReplayGuard(window=60, limit=5, clock=self.clock)

    def stamp(self, offset: float = 0.0) -> str:
        return str(self.clock.now + offset)

    def test_a_fresh_request_passes_and_the_same_nonce_is_a_replay(self):
        nonce = "a" * 20
        self.assertIsNone(self.guard.check("dev:a", self.stamp(), nonce, required=True))
        self.assertEqual(self.guard.check("dev:a", self.stamp(), nonce, required=True), "replayed_nonce")

    def test_the_same_nonce_from_another_identity_is_not_a_replay(self):
        nonce = "b" * 20
        self.assertIsNone(self.guard.check("dev:a", self.stamp(), nonce, required=True))
        self.assertIsNone(self.guard.check("dev:b", self.stamp(), nonce, required=True))

    def test_timestamps_outside_the_window_are_refused_in_both_directions(self):
        self.assertEqual(self.guard.check("d", self.stamp(-61), "c" * 20, True), "stale_timestamp")
        self.assertEqual(self.guard.check("d", self.stamp(61), "d" * 20, True), "stale_timestamp")
        self.assertIsNone(self.guard.check("d", self.stamp(-59), "e" * 20, True))

    def test_malformed_values(self):
        self.assertEqual(self.guard.check("d", "abc", "f" * 20, True), "invalid_timestamp")
        self.assertEqual(self.guard.check("d", "nan", "f" * 20, True), "invalid_timestamp")
        self.assertEqual(self.guard.check("d", "inf", "f" * 20, True), "invalid_timestamp")
        self.assertEqual(self.guard.check("d", self.stamp(), "short", True), "invalid_nonce")
        self.assertEqual(self.guard.check("d", self.stamp(), "x" * 20 + "!", True), "invalid_nonce")
        self.assertEqual(self.guard.check("d", self.stamp(), None, True), "replay_headers_incomplete")
        self.assertEqual(self.guard.check("d", None, "g" * 20, False), "replay_headers_incomplete")

    def test_missing_headers_are_an_error_only_when_required(self):
        self.assertEqual(self.guard.check("d", None, None, required=True), "replay_headers_required")
        self.assertIsNone(self.guard.check("d", None, None, required=False))

    def test_a_nonce_is_forgotten_only_after_its_timestamp_can_no_longer_be_accepted(self):
        nonce = "h" * 20
        self.assertIsNone(self.guard.check("d", self.stamp(), nonce, True))
        self.clock.advance(59)
        self.assertEqual(self.guard.check("d", str(self.clock.now - 58), nonce, True), "replayed_nonce")
        self.clock.advance(62)
        # La finestra e' passata: lo STESSO messaggio (timestamp vecchio) e' rifiutato per eta', non riapplicato.
        self.assertEqual(self.guard.check("d", str(self.clock.now - 121), nonce, True), "stale_timestamp")

    def test_a_full_cache_refuses_instead_of_evicting(self):
        for index in range(5):
            self.assertIsNone(self.guard.check("d", self.stamp(), f"n{index}".ljust(20, "x"), True))
        self.assertEqual(self.guard.check("d", self.stamp(), "z" * 20, True), "replay_cache_full")
        # e il primo nonce NON e' stato espulso: resta un replay.
        self.assertEqual(self.guard.check("d", self.stamp(), "n0".ljust(20, "x"), True), "replayed_nonce")
        self.clock.advance(61)
        self.assertIsNone(self.guard.check("d", self.stamp(), "z" * 20, True))


class ValidationTests(unittest.TestCase):
    def test_command_body_accepts_a_normal_request_and_trims(self):
        fields = validate_command_body({"text": "  apri il calendario  ", "device_id": "phone-1", "session_id": "s_1"})
        self.assertEqual(fields, {"text": "apri il calendario", "device_id": "phone-1", "session_id": "s_1"})

    def test_text_type_length_and_characters(self):
        for bad, code in ((None, "missing_text"), ("", "missing_text"), ("   ", "missing_text"), (5, "invalid_text"),
                          (["a"], "invalid_text"), ("x" * 4001, "text_too_long"), ("ciao\x00mondo", "invalid_text"),
                          ("ciao\x1b[31m", "invalid_text")):
            with self.assertRaises(ValidationError) as ctx:
                validate_command_body({"text": bad})
            self.assertEqual(ctx.exception.code, code, repr(bad))
        self.assertEqual(validate_command_body({"text": "riga1\nriga2\tok"})["text"], "riga1\nriga2\tok")
        self.assertEqual(len(validate_command_body({"text": "x" * 4000})["text"]), 4000)

    def test_identifiers_reject_paths_spaces_and_non_strings(self):
        for bad in ("../etc", "a b", "-x", "x" * 65, 5, {"a": 1}, "a/b", "a\nb"):
            with self.assertRaises(ValidationError):
                validate_identifier(bad, "device_id")
        self.assertEqual(validate_identifier("Telefono-di.Davide:1", "device_id"), "Telefono-di.Davide:1")
        self.assertIsNone(validate_identifier(None, "device_id"))
        with self.assertRaises(ValidationError):
            validate_identifier("", "device_id", required=True)

    def test_claim_name_limits(self):
        self.assertEqual(validate_claim_body({})["name"], "")
        with self.assertRaises(ValidationError):
            validate_claim_body({"name": "n" * 81})
        with self.assertRaises(ValidationError):
            validate_claim_body({"name": 7})
        with self.assertRaises(ValidationError):
            validate_text("a\x00", "name", max_chars=10)


class ProtocolCompatibilityTests(unittest.TestCase):
    def test_window(self):
        self.assertEqual(check_protocol(None)[0], True)
        self.assertTrue(check_protocol(str(PROTOCOL_VERSION))[0])
        self.assertTrue(check_protocol(str(MIN_PROTOCOL_VERSION))[0])
        self.assertFalse(check_protocol(str(PROTOCOL_VERSION + 1))[0])
        self.assertFalse(check_protocol(str(MIN_PROTOCOL_VERSION - 1))[0])
        self.assertFalse(check_protocol("banana")[0])
        _, supported = check_protocol("999")
        self.assertEqual(supported, {"supported_min": MIN_PROTOCOL_VERSION, "supported_max": PROTOCOL_VERSION})


class AuditChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_audit_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "audit.jsonl"

    def test_an_untouched_chain_verifies_and_survives_a_restart(self):
        audit = CompanionAudit(self.path, clock=FakeClock())
        for index in range(4):
            audit.record("command_received", n=index)
        self.assertEqual(audit.verify(), (True, None))
        reopened = CompanionAudit(self.path, clock=FakeClock())
        reopened.record("command_received", n=4)
        self.assertEqual(reopened.verify(), (True, None))
        self.assertEqual([entry["n"] for entry in reopened.read_all()], [0, 1, 2, 3, 4])

    def test_a_modified_line_is_detected_at_its_position(self):
        audit = CompanionAudit(self.path)
        for index in range(4):
            audit.record("e", n=index)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"n": 1', '"n": 9')
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(audit.verify(), (False, 2))

    def test_a_deleted_or_inserted_middle_line_is_detected(self):
        audit = CompanionAudit(self.path)
        for index in range(4):
            audit.record("e", n=index)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
        self.assertEqual(audit.verify(), (False, 2))
        self.path.write_text("\n".join(lines[:2] + [lines[1]] + lines[2:]) + "\n", encoding="utf-8")
        self.assertFalse(audit.verify()[0])

    def test_the_fingerprint_never_carries_the_text(self):
        fingerprint = text_fingerprint("la mia password e' hunter2")
        self.assertEqual(set(fingerprint), {"text_len", "text_sha256"})
        self.assertNotIn("hunter2", json.dumps(fingerprint))


# ---- integrazione: server vero ---------------------------------------------------------------------------------------


class GuardedServerTestCase(unittest.TestCase):
    """Un server con un vero DeviceCredentialStore, due dispositivi e un audit su file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_companion_security_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = DeviceCredentialStore(db_path=self.tmp / "devices.db")
        self.addCleanup(self.store.close)
        self.cred_a = self.store.issue_credential("device-a")
        self.cred_b = self.store.issue_credential("device-b")
        self.calls: list[str] = []
        self.audit = CompanionAudit(self.tmp / "audit.jsonl")

    def start(self, guard: CompanionGuard | None = None, handler=None, **kwargs) -> tuple[CompanionServer, str]:
        guard = guard or CompanionGuard(audit=self.audit)
        if guard.audit is None:
            guard.audit = self.audit
        server = CompanionServer(command_handler=handler or self._handler, credential_store=self.store, guard=guard, **kwargs)
        server.start()
        self.addCleanup(server.stop)
        return server, f"http://127.0.0.1:{server.port}"

    def _handler(self, text: str) -> str:
        self.calls.append(text)
        return f"ok: {text}"

    @staticmethod
    def auth(credential) -> dict:
        return {"Authorization": f"Bearer {credential.token}"}


class BodyAndInputTests(GuardedServerTestCase):
    def test_oversized_body_is_refused_without_reaching_the_handler(self):
        server, base = self.start()
        payload = {"text": "x" * (MAX_BODY_BYTES + 5000)}
        status, body, _ = _call(base, "POST", "/command", payload, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (413, "body_too_large"))
        self.assertEqual(self.calls, [])

    def test_a_claimed_gigantic_length_is_refused_without_waiting_for_the_body(self):
        server, base = self.start()
        started = time.monotonic()
        status, body = _raw(server.port, "POST", "/command", {**self.auth(self.cred_a), "Content-Length": str(10 ** 9)})
        self.assertEqual((status, body["error"]), (413, "body_too_large"))
        self.assertLess(time.monotonic() - started, 3)

    def test_malformed_bodies_are_400_and_never_call_the_handler(self):
        server, base = self.start()
        auth = self.auth(self.cred_a)
        cases = [
            (b"{not json", "invalid_json"),
            (b"\xff\xfe", "invalid_json"),
            (b"[1, 2]", "body_must_be_object"),
            (b"\"testo\"", "body_must_be_object"),
            (b"{\"text\": 5}", "invalid_text"),
            (b"{\"text\": [\"a\"]}", "invalid_text"),
            (b"{\"text\": \"a\\u0000b\"}", "invalid_text"),
            (json.dumps({"text": "x" * 4001}).encode(), "text_too_long"),
            (b"{\"text\": \"ok\", \"device_id\": \"../x\"}", "invalid_device_id"),
            (b"{\"text\": \"ok\", \"session_id\": 4}", "invalid_session_id"),
            (b"{}", "missing_text"),
        ]
        for raw, code in cases:
            status, body = _raw(server.port, "POST", "/command", {**auth, "Content-Length": str(len(raw))}, raw)
            self.assertEqual((status, body["error"]), (400, code), raw)
        self.assertEqual(self.calls, [])

    def test_bad_content_length_and_chunked_encoding(self):
        server, base = self.start()
        auth = self.auth(self.cred_a)
        for value in ("abc", "-5", "1.5", "0x10"):
            status, body = _raw(server.port, "POST", "/command", {**auth, "Content-Length": value})
            self.assertEqual((status, body["error"]), (400, "invalid_content_length"), value)
        status, body = _raw(server.port, "POST", "/command", {**auth, "Transfer-Encoding": "chunked"},
                            b"4\r\ntext\r\n0\r\n\r\n")
        self.assertEqual((status, body["error"]), (411, "length_required"))
        self.assertEqual(self.calls, [])

    def test_a_truncated_body_is_400(self):
        server, base = self.start()
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
            sock.sendall((f"POST /command HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {self.cred_a.token}\r\n"
                          "Content-Length: 50\r\n\r\n{\"text\":").encode())
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:  # intestazioni e corpo possono arrivare in segmenti diversi: si legge fino alla chiusura
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
            reply = b"".join(chunks).decode("utf-8", "replace")
        self.assertIn("400", reply.splitlines()[0])
        self.assertIn("truncated_body", reply)
        self.assertEqual(self.calls, [])

    def test_claim_validates_the_path_and_the_name(self):
        server, base = self.start()
        status, body, _ = _call(base, "POST", "/devices/device-a/claim", {"name": "n" * 200}, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (400, "name_too_long"))
        status, body, _ = _call(base, "POST", "/devices/device-a/extra/claim", {}, self.auth(self.cred_a))
        self.assertEqual(status, 404)
        status, body, _ = _call(base, "POST", "/devices/%20bad/claim", {}, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (400, "invalid_device_id"))

    def test_a_handler_that_raises_is_a_500_and_an_audit_line(self):
        def boom(text):
            raise RuntimeError("segreto interno")
        server, base = self.start(handler=boom)
        status, body, _ = _call(base, "POST", "/command", {"text": "ciao"}, self.auth(self.cred_a))
        self.assertEqual((status, body), (500, {"error": "command_failed"}))
        self.assertNotIn("segreto", json.dumps(body))
        events = [entry["event"] for entry in self.audit.read_all()]
        self.assertEqual(events, ["command_received", "command_failed"])
        self.assertEqual(self.audit.read_all()[-1]["error"], "RuntimeError")


class RateLimitIntegrationTests(GuardedServerTestCase):
    def test_a_device_over_its_limit_gets_429_with_retry_after_while_another_is_unaffected(self):
        limiter = RateLimiter({EndpointClass.COMMAND: Limit(burst=2, per_second=0.01)})
        server, base = self.start(CompanionGuard(rate_limiter=limiter))
        for _ in range(2):
            self.assertEqual(_call(base, "POST", "/command", {"text": "a"}, self.auth(self.cred_a))[0], 200)
        status, body, headers = _call(base, "POST", "/command", {"text": "a"}, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (429, "rate_limited"))
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        self.assertEqual(_call(base, "POST", "/command", {"text": "b"}, self.auth(self.cred_b))[0], 200)
        self.assertEqual(self.calls, ["a", "a", "b"])
        # la lettura ha un secchio suo: un dispositivo limitato nei comandi puo' ancora vedere lo stato
        self.assertEqual(_call(base, "GET", "/status", None, self.auth(self.cred_a))[0], 200)

    def test_repeated_bad_tokens_lock_the_address_and_a_correct_token_is_not_even_evaluated(self):
        clock = FakeClock()
        throttle = AuthFailureThrottle(max_failures=3, window=60, lockout=30, clock=clock)
        server, base = self.start(CompanionGuard(auth_throttle=throttle))
        for _ in range(3):
            self.assertEqual(_call(base, "GET", "/status", None, {"Authorization": "Bearer sbagliato"})[0], 401)
        status, body, headers = _call(base, "GET", "/status", None, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (429, "too_many_auth_failures"))
        self.assertIn("Retry-After", headers)
        clock.advance(31)
        self.assertEqual(_call(base, "GET", "/status", None, self.auth(self.cred_a))[0], 200)

    def test_a_valid_client_polling_does_not_reset_an_attackers_failure_count(self):
        throttle = AuthFailureThrottle(max_failures=4, window=60, lockout=30, clock=FakeClock())
        server, base = self.start(CompanionGuard(auth_throttle=throttle))
        for _ in range(3):
            self.assertEqual(_call(base, "GET", "/status", None, {"Authorization": "Bearer x"})[0], 401)
            self.assertEqual(_call(base, "GET", "/status", None, self.auth(self.cred_a))[0], 200)
        self.assertEqual(_call(base, "GET", "/status", None, {"Authorization": "Bearer x"})[0], 401)
        self.assertEqual(_call(base, "GET", "/status", None, self.auth(self.cred_a))[0], 429)


class CapabilityIntegrationTests(GuardedServerTestCase):
    def test_a_read_only_device_reads_but_can_never_command_or_claim(self):
        guard = CompanionGuard()
        guard.set_capabilities("device-a", [EndpointClass.READ_ONLY])
        server, base = self.start(guard)
        self.assertEqual(_call(base, "GET", "/status", None, self.auth(self.cred_a))[0], 200)
        status, body, _ = _call(base, "POST", "/command", {"text": "spegni tutto"}, self.auth(self.cred_a))
        self.assertEqual((status, body), (403, {"error": "capability_denied", "required": "command"}))
        status, body, _ = _call(base, "POST", "/devices/device-a/claim", {}, self.auth(self.cred_a))
        self.assertEqual(status, 403)
        self.assertEqual(self.calls, [])
        self.assertIsNone(server.devices.active_device_id)
        # l'altro dispositivo, senza restrizioni, comanda normalmente
        self.assertEqual(_call(base, "POST", "/command", {"text": "ok"}, self.auth(self.cred_b))[0], 200)
        self.assertEqual(self.calls, ["ok"])

    def test_file_and_audio_are_denied_by_default_even_before_endpoints_exist(self):
        server, base = self.start()
        status, body, _ = _call(base, "POST", "/files/upload", {}, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (403, "capability_denied"))
        status, body, _ = _call(base, "POST", "/audio/stream", {}, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (403, "capability_denied"))
        # concessa la classe, si arriva al 404 (l'endpoint non esiste ancora): la capability non inventa route
        guard = CompanionGuard()
        guard.set_capabilities("device-a", [EndpointClass.FILE])
        server2, base2 = self.start(guard)
        self.assertEqual(_call(base2, "POST", "/files/upload", {}, self.auth(self.cred_a))[0], 404)


class ReplayIntegrationTests(GuardedServerTestCase):
    def headers(self, credential, nonce: str, stamp: float | None = None) -> dict:
        return {**self.auth(credential), "X-Jake-Nonce": nonce, "X-Jake-Timestamp": str(stamp if stamp is not None else time.time())}

    def test_required_replay_protection_rejects_missing_headers_and_replays(self):
        server, base = self.start(CompanionGuard(require_replay_protection=True))
        status, body, _ = _call(base, "POST", "/command", {"text": "a"}, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (400, "replay_headers_required"))
        headers = self.headers(self.cred_a, "n" * 24)
        self.assertEqual(_call(base, "POST", "/command", {"text": "a"}, headers)[0], 200)
        status, body, _ = _call(base, "POST", "/command", {"text": "a"}, headers)
        self.assertEqual((status, body["error"]), (409, "replayed_nonce"))
        self.assertEqual(self.calls, ["a"])

    def test_a_captured_request_replayed_by_a_third_party_is_rejected(self):
        server, base = self.start(CompanionGuard(require_replay_protection=True))
        captured = self.headers(self.cred_a, "c" * 24)
        self.assertEqual(_call(base, "POST", "/command", {"text": "sblocca la porta"}, captured)[0], 200)
        for _ in range(3):
            self.assertEqual(_call(base, "POST", "/command", {"text": "sblocca la porta"}, captured)[0], 409)
        self.assertEqual(len(self.calls), 1)

    def test_stale_timestamp_reports_the_server_time_so_a_skewed_clock_can_fix_itself(self):
        server, base = self.start(CompanionGuard(require_replay_protection=True))
        status, body, _ = _call(base, "POST", "/command", {"text": "a"},
                                self.headers(self.cred_a, "s" * 24, time.time() - 3600))
        self.assertEqual((status, body["error"]), (400, "stale_timestamp"))
        self.assertAlmostEqual(body["server_time"], time.time(), delta=5)

    def test_headers_are_optional_on_loopback_and_validated_when_sent(self):
        server, base = self.start()
        self.assertEqual(_call(base, "POST", "/command", {"text": "a"}, self.auth(self.cred_a))[0], 200)
        headers = self.headers(self.cred_a, "corto")
        self.assertEqual(_call(base, "POST", "/command", {"text": "b"}, headers)[0], 400)
        self.assertEqual(self.calls, ["a"])

    def test_reading_never_requires_replay_headers(self):
        server, base = self.start(CompanionGuard(require_replay_protection=True))
        self.assertEqual(_call(base, "GET", "/status", None, self.auth(self.cred_a))[0], 200)


class ProtocolIntegrationTests(GuardedServerTestCase):
    def test_incompatible_client_gets_426_with_the_supported_window(self):
        server, base = self.start()
        status, body, _ = _call(base, "GET", "/status", None, {**self.auth(self.cred_a), "X-Jake-Protocol": str(PROTOCOL_VERSION + 5)})
        self.assertEqual(status, 426)
        self.assertEqual(body["supported_max"], PROTOCOL_VERSION)
        self.assertEqual(_call(base, "GET", "/status", None, {**self.auth(self.cred_a), "X-Jake-Protocol": str(PROTOCOL_VERSION)})[0], 200)
        status, body, _ = _call(base, "GET", "/status", None, self.auth(self.cred_a))
        self.assertEqual(status, 200)
        self.assertEqual(body["min_protocol_version"], MIN_PROTOCOL_VERSION)
        self.assertFalse(body["encrypted"])


class AuditIntegrationTests(GuardedServerTestCase):
    def test_every_remote_command_and_handoff_leaves_a_line_and_never_the_text(self):
        server, base = self.start()
        secret = "la password del wifi e' correttocavallo"
        _call(base, "POST", "/command", {"text": secret}, self.auth(self.cred_a))
        _call(base, "POST", "/devices/device-a/claim", {"name": "Telefono"}, self.auth(self.cred_a))
        _call(base, "POST", "/devices/device-b/claim", {"name": "Tablet"}, self.auth(self.cred_b))
        _call(base, "POST", "/devices/device-b/release", {}, self.auth(self.cred_b))
        entries = self.audit.read_all()
        events = [entry["event"] for entry in entries]
        self.assertEqual(events, ["command_received", "command_completed", "handoff_claim", "handoff_claim", "handoff_release"])
        received = entries[0]
        self.assertEqual(received["device_id"], "device-a")
        self.assertTrue(received["authenticated"])
        self.assertEqual(received["endpoint"], "/command")
        self.assertEqual(received["endpoint_class"], "command")
        self.assertEqual(received["text_len"], len(secret))
        self.assertEqual(received["text_sha256"], text_fingerprint(secret)["text_sha256"])
        self.assertEqual(entries[3]["previous_device"], "device-a")
        self.assertEqual(entries[3]["target_device"], "device-b")
        self.assertEqual(entries[1]["request_id"], received["request_id"])
        raw = (self.tmp / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("correttocavallo", raw)
        self.assertNotIn(self.cred_a.token, raw)
        self.assertEqual(self.audit.verify(), (True, None))

    def test_rejections_are_audited_with_their_reason(self):
        guard = CompanionGuard()
        guard.set_capabilities("device-a", [EndpointClass.READ_ONLY])
        server, base = self.start(guard)
        _call(base, "GET", "/status", None, {"Authorization": "Bearer sbagliato"})
        _call(base, "POST", "/command", {"text": "x"}, self.auth(self.cred_a))
        _call(base, "POST", "/command", {"text": 5}, self.auth(self.cred_b))
        reasons = [(entry["event"], entry.get("reason")) for entry in self.audit.read_all()]
        self.assertEqual(reasons, [("auth_failed", "unauthorized"), ("rejected", "capability_denied"), ("rejected", "invalid_text")])
        self.assertFalse(self.audit.read_all()[0]["authenticated"])
        self.assertEqual(self.audit.read_all()[1]["device_id"], "device-a")

    def test_if_the_audit_cannot_be_written_the_command_does_not_run(self):
        broken = CompanionAudit(self.tmp)  # una CARTELLA: aprirla in append fallisce
        server, base = self.start(CompanionGuard(audit=broken))
        status, body, _ = _call(base, "POST", "/command", {"text": "cancella tutto"}, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (503, "audit_unavailable"))
        self.assertEqual(self.calls, [])

    def test_successful_reads_are_not_audited_but_streams_are(self):
        server, base = self.start()
        _call(base, "GET", "/status", None, self.auth(self.cred_a))
        self.assertEqual(self.audit.read_all(), [])


class StreamLimitTests(GuardedServerTestCase):
    def test_concurrent_streams_per_identity_are_bounded(self):
        server, base = self.start(CompanionGuard(max_streams_per_identity=1))
        first = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        self.addCleanup(first.close)
        first.sendall(f"GET /events HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {self.cred_a.token}\r\n\r\n".encode())
        self.assertIn(b"200", first.recv(1024))
        status, body, _ = _call(base, "GET", "/events", None, self.auth(self.cred_a))
        self.assertEqual((status, body["error"]), (429, "too_many_streams"))
        # un altro dispositivo ha il suo budget
        second = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        self.addCleanup(second.close)
        second.sendall(f"GET /events HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {self.cred_b.token}\r\n\r\n".encode())
        self.assertIn(b"200", second.recv(1024))


class PenetrationTests(GuardedServerTestCase):
    """Criterio di uscita F7.1: nessun comando senza un dispositivo autorizzato. Un attaccante locale prova ogni
    strada che il server espone e il gestore dei comandi non deve mai girare."""

    def test_no_command_without_an_authorized_device(self):
        server, base = self.start(CompanionGuard(require_replay_protection=True, auth_throttle=AuthFailureThrottle(max_failures=1000)))
        self.store.revoke("device-b")
        good_nonce = 0

        def nonce() -> str:
            nonlocal good_nonce
            good_nonce += 1
            return f"attack{good_nonce:04d}".ljust(24, "0")

        def fresh(headers: dict) -> dict:
            return {**headers, "X-Jake-Nonce": nonce(), "X-Jake-Timestamp": str(time.time())}

        attempts = [
            fresh({}),
            fresh({"Authorization": "Bearer"}),
            fresh({"Authorization": "Bearer "}),
            fresh({"Authorization": "Bearer inventato"}),
            fresh({"Authorization": f"Bearer {self.cred_a.token[:-1]}"}),
            fresh({"Authorization": f"bearer {self.cred_a.token}"}),
            fresh({"Authorization": f"Basic {self.cred_a.token}"}),
            fresh(self.auth(self.cred_b)),  # revocato
        ]
        for headers in attempts:
            status, _, _ = _call(base, "POST", "/command", {"text": "comando ostile"}, headers)
            self.assertEqual(status, 401, headers)
        # senza header di replay e con token valido di un dispositivo: non e' un attacco, ma senza anti-replay non passa
        self.assertEqual(_call(base, "POST", "/command", {"text": "comando ostile"}, self.auth(self.cred_a))[0], 400)
        # un dispositivo valido non puo' agire a nome di un altro
        headers = fresh(self.auth(self.cred_a))
        self.assertEqual(_call(base, "POST", "/devices/device-b/claim", {}, headers)[0], 403)
        self.assertEqual(self.calls, [])
        self.assertIsNone(server.devices.active_device_id)

    def test_garbage_on_the_socket_does_not_take_the_server_down(self):
        server, base = self.start()
        for junk in (b"\x00\x01\x02\r\n\r\n", b"GET\r\n\r\n", b"POST /command HTTP/1.1\r\n\r\n" + b"A" * 100_000,
                     b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03" + b"\x00" * 64):
            with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
                try:
                    sock.sendall(junk)
                    sock.shutdown(socket.SHUT_WR)
                    sock.recv(4096)
                except OSError:
                    pass
        self.assertEqual(_call(base, "GET", "/status", None, self.auth(self.cred_a))[0], 200)
        self.assertEqual(self.calls, [])

    def test_a_silent_connection_is_dropped_after_the_request_timeout(self):
        original = companion_server._Handler.timeout
        companion_server._Handler.timeout = 1
        self.addCleanup(setattr, companion_server._Handler, "timeout", original)
        server, base = self.start()
        with socket.create_connection(("127.0.0.1", server.port), timeout=6) as sock:
            sock.sendall(b"POST /command HTTP/1.1\r\nHost: x\r\n")  # intestazioni mai terminate (slowloris)
            started = time.monotonic()
            self.assertEqual(sock.recv(1024), b"")
            self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(_call(base, "GET", "/status", None, self.auth(self.cred_a))[0], 200)


# ---- TLS vero ------------------------------------------------------------------------------------------------------------


def make_self_signed(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "jake-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


class TlsTests(GuardedServerTestCase):
    def setUp(self):
        super().setUp()
        self.cert, self.key = make_self_signed(self.tmp)
        self.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.server_context.load_cert_chain(self.cert, self.key)
        self.client_context = ssl.create_default_context(cafile=str(self.cert))

    def test_a_real_tls_handshake_carries_status_command_and_audit(self):
        server, _ = self.start(tls_context=self.server_context)
        base = f"https://localhost:{server.port}"
        status, body, _ = _call(base, "GET", "/status", None, self.auth(self.cred_a), context=self.client_context)
        self.assertEqual(status, 200)
        self.assertTrue(body["encrypted"])
        status, body, _ = _call(base, "POST", "/command", {"text": "via tls"}, self.auth(self.cred_a), context=self.client_context)
        self.assertEqual((status, body["response"]), (200, "ok: via tls"))
        self.assertEqual(self.calls, ["via tls"])

    def test_the_token_is_not_readable_on_the_wire(self):
        server, _ = self.start(tls_context=self.server_context)
        captured = bytearray()
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as raw:
            with self.client_context.wrap_socket(raw, server_hostname="localhost") as tls:
                tls.sendall(f"GET /status HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {self.cred_a.token}\r\n\r\n".encode())
                self.assertIn(b"200", tls.recv(4096))
        # Cio' che un ascoltatore vede e' il flusso cifrato: lo si riproduce con un proxy che registra i byte.
        proxy_listener = socket.socket()
        proxy_listener.bind(("127.0.0.1", 0))
        proxy_listener.listen(1)
        self.addCleanup(proxy_listener.close)

        def relay():
            client, _ = proxy_listener.accept()
            upstream = socket.create_connection(("127.0.0.1", server.port), timeout=5)
            client.settimeout(2)
            upstream.settimeout(2)
            try:
                for _ in range(20):
                    for source, sink in ((client, upstream), (upstream, client)):
                        try:
                            data = source.recv(65536)
                        except (TimeoutError, OSError):
                            continue
                        if data:
                            captured.extend(data)
                            sink.sendall(data)
            finally:
                client.close()
                upstream.close()

        thread = threading.Thread(target=relay, daemon=True)
        thread.start()
        with socket.create_connection(("127.0.0.1", proxy_listener.getsockname()[1]), timeout=5) as raw:
            with self.client_context.wrap_socket(raw, server_hostname="localhost") as tls:
                tls.sendall(f"GET /status HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {self.cred_a.token}\r\n\r\n".encode())
                tls.recv(4096)
        thread.join(timeout=10)
        self.assertGreater(len(captured), 100)
        self.assertNotIn(self.cred_a.token.encode(), bytes(captured))
        self.assertNotIn(b"Authorization", bytes(captured))

    def test_plain_http_to_a_tls_port_gets_nothing_and_a_client_that_does_not_trust_the_cert_fails(self):
        server, _ = self.start(tls_context=self.server_context)
        with self.assertRaises((error.URLError, ConnectionError, http.client.HTTPException, OSError)):
            _call(f"http://127.0.0.1:{server.port}", "GET", "/status", None, self.auth(self.cred_a), timeout=3)
        with self.assertRaises(error.URLError) as ctx:
            _call(f"https://localhost:{server.port}", "GET", "/status", None, self.auth(self.cred_a))
        self.assertIsInstance(ctx.exception.reason, ssl.SSLCertVerificationError)
        self.assertEqual(self.calls, [])
        # e il server continua a servire chi si fida del certificato
        status, _, _ = _call(f"https://localhost:{server.port}", "GET", "/status", None, self.auth(self.cred_a), context=self.client_context)
        self.assertEqual(status, 200)

    def test_a_stalled_handshake_does_not_block_other_clients(self):
        server, _ = self.start(tls_context=self.server_context)
        stalled = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        self.addCleanup(stalled.close)  # connesso, nessun ClientHello
        started = time.monotonic()
        status, _, _ = _call(f"https://localhost:{server.port}", "GET", "/status", None, self.auth(self.cred_a), context=self.client_context)
        self.assertEqual(status, 200)
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
