"""Test unitari per core/forge_worker.py (F1.6, worker persistente per skill forgiate): nessuna
suite esisteva finora. Gira normalmente come script standalone in un processo sandboxato separato
(vedi core/sandboxed_skill_worker.py); qui si chiama main() direttamente in-process, con
sys.stdin/sys.stdout rimpiazzati da veri oggetti StringIO (il protocollo a righe JSON e' l'intera
interfaccia del componente, stesso principio gia' usato in tests/test_forge_probe.py per il file
di I/O di quel modulo gemello)."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.forge_worker import main

ECHO_PLUGIN = """
from core.skill_result import SkillResult

class EchoSkill:
    metadata = {"intent": "ECHO_TEST", "description": "test", "parameters": {}}

    def execute(self, parameters=None):
        parameters = parameters or {}
        return SkillResult(success=True, data={"echo": parameters.get("text")})


def register(registry):
    registry.register_skill("ECHO_TEST", EchoSkill())
"""

RAISING_PLUGIN = """
class RaisingSkill:
    metadata = {"intent": "RAISE_TEST", "description": "test", "parameters": {}}

    def execute(self, parameters=None):
        raise RuntimeError("boom")


def register(registry):
    registry.register_skill("RAISE_TEST", RaisingSkill())
"""


def _run_worker(plugin_sources: list, requests: list) -> list:
    with tempfile.TemporaryDirectory() as tmp_dir:
        plugin_paths = []
        for i, source in enumerate(plugin_sources):
            path = Path(tmp_dir) / f"plugin_{i}.py"
            path.write_text(source, encoding="utf-8")
            plugin_paths.append(str(path))
        input_text = "\n".join(json.dumps(r) for r in requests) + "\n"
        stdin = io.StringIO(input_text)
        stdout = io.StringIO()
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
            main("", plugin_paths)
        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        return [json.loads(line) for line in lines]


class StartupSignalTests(unittest.TestCase):
    def test_first_line_is_a_ready_signal(self):
        responses = _run_worker([ECHO_PLUGIN], [])
        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0]["ready"])


class InvocationProtocolTests(unittest.TestCase):
    def test_a_known_intent_executes_and_echoes_the_result(self):
        responses = _run_worker([ECHO_PLUGIN], [{"intent": "ECHO_TEST", "parameters": {"text": "ciao"}}])
        self.assertEqual(responses[1], {"success": True, "data": {"echo": "ciao"}, "error": None})

    def test_an_unknown_intent_reports_an_error_without_crashing_the_worker(self):
        responses = _run_worker([ECHO_PLUGIN], [
            {"intent": "NOT_REGISTERED", "parameters": {}},
            {"intent": "ECHO_TEST", "parameters": {"text": "ancora vivo"}},
        ])
        self.assertFalse(responses[1]["success"])
        self.assertIn("NOT_REGISTERED", responses[1]["error"])
        self.assertEqual(responses[2], {"success": True, "data": {"echo": "ancora vivo"}, "error": None})

    def test_a_skill_that_raises_produces_an_error_response_not_a_crash(self):
        """Il worker e' condiviso da tutte le skill forgiate: un'eccezione in UNA non deve mai
        far perdere il processo (e con esso la capacita' di servire le altre)."""
        responses = _run_worker([RAISING_PLUGIN], [
            {"intent": "RAISE_TEST", "parameters": {}},
            {"intent": "RAISE_TEST", "parameters": {}},
        ])
        self.assertFalse(responses[1]["success"])
        self.assertIn("RuntimeError: boom", responses[1]["error"])
        self.assertFalse(responses[2]["success"])  # ancora vivo per la seconda chiamata

    def test_a_malformed_request_line_produces_an_error_response_not_a_crash(self):
        stdin = io.StringIO("questo non e' json\n")
        stdout = io.StringIO()
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
            main("", [])
        lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertFalse(lines[1]["success"])
        self.assertIn("JSON", lines[1]["error"])

    def test_shutdown_message_stops_reading_further_requests(self):
        responses = _run_worker([ECHO_PLUGIN], [
            {"shutdown": True},
            {"intent": "ECHO_TEST", "parameters": {"text": "mai eseguito"}},
        ])
        self.assertEqual(len(responses), 1, "solo il segnale di avvio: shutdown ferma il ciclo prima di leggere altro")

    def test_a_broken_plugin_does_not_prevent_the_others_from_loading(self):
        responses = _run_worker(["questo non e' python valido!!!", ECHO_PLUGIN], [
            {"intent": "ECHO_TEST", "parameters": {"text": "sopravvive"}},
        ])
        self.assertEqual(responses[1], {"success": True, "data": {"echo": "sopravvive"}, "error": None})


if __name__ == "__main__":
    unittest.main()
