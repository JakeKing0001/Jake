import unittest
from unittest import mock

from core import ollama_client
from core.ollama_client import OllamaClient, keep_alive_for


class KeepAlivePolicyTests(unittest.TestCase):
    def _with(self, **settings):
        base = {"primary": "qwen2.5:7b", "low_memory": False}
        base.update(settings)
        return mock.patch.object(ollama_client, "_settings_cache", base)

    def test_primary_stays_warm_secondary_unloads_soon(self):
        with self._with():
            self.assertEqual(keep_alive_for("qwen2.5:7b"), "30m")
            self.assertEqual(keep_alive_for("qwen2.5vl:7b"), "2m")

    def test_low_memory_and_overrides(self):
        with self._with(low_memory=True):
            self.assertEqual(keep_alive_for("qwen2.5:7b"), "5m")
            self.assertEqual(keep_alive_for("nomic-embed-text"), "0")
        with self._with(secondary_keep_alive="10m"):
            self.assertEqual(keep_alive_for("qwen2.5-coder:7b"), "10m")

    def test_client_uses_policy_unless_explicit(self):
        with self._with():
            client = OllamaClient()
            with mock.patch.object(client, "_post", return_value={}) as post:
                client.chat("qwen2.5vl:7b", [])
            self.assertEqual(post.call_args[0][1]["keep_alive"], "2m")
            client = OllamaClient(keep_alive="1h")
            with mock.patch.object(client, "_post", return_value={}) as post:
                client.chat("qwen2.5vl:7b", [])
            self.assertEqual(post.call_args[0][1]["keep_alive"], "1h")


if __name__ == "__main__":
    unittest.main()
