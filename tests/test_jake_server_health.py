"""F9.1: `GET /health` di Jake Server (jake_server/health.py) su un'istanza vera, porta effimera su 127.0.0.1."""
import json
import unittest
from urllib import error, request

from core.companion_guard import EndpointClass, Limit, RateLimiter
from core.version import PROTOCOL_VERSION, VERSION
from jake_server.health import HealthServer


def _call(url: str, method: str = "GET", data: bytes | None = None):
    req = request.Request(url, data=data, method=method)
    try:
        with request.urlopen(req, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except error.HTTPError as exc:
        with exc:
            return exc.code, dict(exc.headers), exc.read()


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server = HealthServer(host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base = f"http://127.0.0.1:{self.server.port}"

    def test_health_returns_the_documented_schema_and_nothing_else(self):
        status, headers, body = _call(self.base + "/health")

        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        payload = json.loads(body)
        # Chiavi esatte: un campo nuovo (host, IP, percorsi...) deve essere una scelta, non una fuga per sbaglio.
        self.assertEqual(set(payload), {"status", "service", "version", "protocol_version", "node", "uptime_s"})
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "jake-server")
        self.assertEqual(payload["node"], "server")
        self.assertEqual(payload["version"], VERSION)
        self.assertEqual(payload["protocol_version"], PROTOCOL_VERSION)
        self.assertGreaterEqual(payload["uptime_s"], 0)

    def test_server_header_does_not_reveal_the_python_version(self):
        _, headers, _ = _call(self.base + "/health")

        self.assertEqual(headers["Server"], "jake-server")

    def test_head_answers_without_a_body(self):
        status, _, body = _call(self.base + "/health", method="HEAD")

        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_only_health_exists(self):
        for path in ("/", "/status", "/command", "/health/../status", "/healthz"):
            with self.subTest(path=path):
                status, _, body = _call(self.base + path)
                self.assertEqual(status, 404)
                self.assertEqual(json.loads(body), {"error": "not_found"})

    def test_no_method_that_carries_a_body_is_accepted(self):
        for method in ("POST", "PUT", "DELETE"):
            with self.subTest(method=method):
                status, _, _ = _call(self.base + "/health", method=method, data=b"{}")
                self.assertEqual(status, 501)


class HealthRateLimitTests(unittest.TestCase):
    def test_flood_from_one_address_is_rate_limited(self):
        limiter = RateLimiter(limits={EndpointClass.READ_ONLY: Limit(2, 0.01)})
        server = HealthServer(host="127.0.0.1", port=0, rate_limiter=limiter)
        server.start()
        self.addCleanup(server.stop)
        url = f"http://127.0.0.1:{server.port}/health"

        statuses = [_call(url)[0] for _ in range(3)]
        status, headers, _ = _call(url)

        self.assertEqual(statuses[:2], [200, 200])
        self.assertEqual(status, 429)
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)


if __name__ == "__main__":
    unittest.main()
