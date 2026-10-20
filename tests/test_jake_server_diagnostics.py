"""F9.2: lo stato di Jake Server nel bundle di `python main.py --diagnostics` (tools/diagnostic_bundle.py) - con il
server acceso, spento o non configurato il bundle si genera sempre, e non porta ne' URL ne' IP del server."""
import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from core.version import PROTOCOL_VERSION
from jake_server.health import HealthServer
from tools.diagnostic_bundle import collect_jake_server, main, redact_config


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CollectJakeServerTests(unittest.TestCase):
    def test_online_server_is_reported_without_its_address(self):
        server = HealthServer(host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)

        section = collect_jake_server({"server_url": f"http://127.0.0.1:{server.port}"})

        self.assertEqual(section["state"], "online")
        self.assertEqual(section["version"], server.payload()["version"])
        self.assertEqual(section["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(section["capabilities"], ["health"])
        self.assertIsInstance(section["latency_ms"], float)
        self.assertLess(section["last_success_age_s"], 5)
        text = json.dumps(section)
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn(str(server.port), text)

    def test_offline_server_is_reported_and_never_raises(self):
        section = collect_jake_server({"server_url": f"http://127.0.0.1:{_free_port()}",
                                       "server_timeout_seconds": 0.3})

        self.assertEqual(section["state"], "offline")
        self.assertIsNone(section["last_success_age_s"])
        self.assertEqual(section["capabilities"], [])

    def test_unconfigured_server_makes_no_request(self):
        section = collect_jake_server({})

        self.assertEqual((section["state"], section["reason"]), ("disabled", "not_configured"))

    def test_server_url_is_redacted_in_the_shared_configuration(self):
        redacted = redact_config({"server_url": "http://192.168.1.13:8770", "ollama_model": "qwen2.5:7b"})

        self.assertEqual(redacted["server_url"], "<redacted>")
        self.assertEqual(redact_config({"server_url": ""})["server_url"], "")
        self.assertEqual(redacted["ollama_model"], "qwen2.5:7b")


class BundleWithServerOfflineTests(unittest.TestCase):
    def test_the_bundle_is_written_even_with_the_server_offline(self):
        url = f"http://127.0.0.1:{_free_port()}"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle.json"
            missing = str(Path(tmp) / "missing.jsonl")
            env = {"JAKE_SERVER_URL": url, "JAKE_SERVER_TIMEOUT_SECONDS": "0.3"}
            with mock.patch.dict(os.environ, env), redirect_stdout(io.StringIO()):
                main(["--actions-path", missing, "--ledger-path", missing, "--sessions-path", missing,
                      "--output", str(out)])
            bundle = json.loads(out.read_text(encoding="utf-8"))

        section = bundle["environment"]["jake_server"]
        self.assertEqual(section["state"], "offline")
        self.assertNotIn(url, json.dumps(bundle))


if __name__ == "__main__":
    unittest.main()
