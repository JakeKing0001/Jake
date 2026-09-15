"""Test unitari per core/forge_worker.py (F1.6, worker persistente per skill forgiate): nessuna
suite esisteva finora. Gira normalmente come script standalone in un processo sandboxato separato
(vedi core/sandboxed_skill_worker.py); qui si chiama main() direttamente in-process, con
sys.stdin/sys.stdout rimpiazzati da veri oggetti StringIO (il protocollo a righe JSON e' l'intera
interfaccia del componente, stesso principio gia' usato in tests/test_forge_probe.py per il file
di I/O di quel modulo gemello).

F1.6.5: main() installa un gate su builtins.open/os.open che NON verrebbe mai disinstallato in un
worker vero (gira come processo separato, usa e getta - vedi core/sandboxed_skill_worker.py). Qui
main() gira invece IN PROCESSO con questa stessa suite: senza ripristinarli esplicitamente dopo
ogni chiamata, il gate resterebbe installato per DAVVERO su questo processo di test, rompendo
silenziosamente qualunque open() successivo in test completamente estranei. _run_worker() lo fa
sempre, in un finally, indipendentemente da quale test lo chiami."""
import builtins
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.forge_worker as forge_worker
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


def _read_file_plugin(intent: str, allowed_path: str | None = None) -> str:
    """Skill che apre il percorso passato in 'path' e restituisce il contenuto - usata per
    esercitare il gate F1.6.5. allowed_path=None -> nessun MANIFEST dichiarato (nega per
    default); altrimenti dichiara quel percorso come l'unico concesso durante execute()."""
    manifest_line = ""
    if allowed_path is not None:
        escaped = allowed_path.replace("\\", "\\\\")
        manifest_line = f"MANIFEST = {{'allowed_paths': ['{escaped}']}}\n"
    return (
        manifest_line
        + "from core.skill_result import SkillResult\n"
        "class ReadFileSkill:\n"
        f"    metadata = {{'intent': '{intent}', 'description': 'test', 'parameters': {{}}}}\n"
        "    def execute(self, parameters=None):\n"
        "        parameters = parameters or {}\n"
        "        with open(parameters['path'], 'r', encoding='utf-8') as f:\n"
        "            return SkillResult(success=True, data={'content': f.read()})\n"
        "def register(registry):\n"
        f"    registry.register_skill('{intent}', ReadFileSkill())\n"
    )


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
        try:
            with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
                main("", plugin_paths)
        finally:
            # F1.6.5: vedi il docstring del modulo - il gate va sempre tolto da QUESTO processo.
            builtins.open = forge_worker._real_open
            os.open = forge_worker._real_os_open
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
        try:
            with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
                main("", [])
        finally:
            builtins.open = forge_worker._real_open
            os.open = forge_worker._real_os_open
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


class ManifestPathGateTests(unittest.TestCase):
    """F1.6.5 ("montare soltanto directory dichiarate nel manifest"): gate applicativo su
    builtins.open/os.open, NON garantito dal kernel (vedi il docstring di core/forge_worker.py
    sul perche' - nega per default, un manifest esplicito concede solo quel percorso)."""

    def test_a_skill_without_a_manifest_cannot_open_any_file(self):
        with tempfile.TemporaryDirectory() as target_dir:
            target = Path(target_dir) / "segreto.txt"
            target.write_text("contenuto privato", encoding="utf-8")

            responses = _run_worker(
                [_read_file_plugin("READ_TEST")],
                [{"intent": "READ_TEST", "parameters": {"path": str(target)}}],
            )

            self.assertFalse(responses[1]["success"])
            self.assertIn("PermissionError", responses[1]["error"])

    def test_a_skill_can_open_a_file_inside_its_declared_manifest_path(self):
        with tempfile.TemporaryDirectory() as target_dir:
            target = Path(target_dir) / "dati.txt"
            target.write_text("contenuto permesso", encoding="utf-8")

            responses = _run_worker(
                [_read_file_plugin("READ_TEST", allowed_path=target_dir)],
                [{"intent": "READ_TEST", "parameters": {"path": str(target)}}],
            )

            self.assertEqual(responses[1], {"success": True, "data": {"content": "contenuto permesso"}, "error": None})

    def test_a_skill_cannot_open_a_file_outside_its_declared_manifest_path(self):
        with tempfile.TemporaryDirectory() as allowed_dir, tempfile.TemporaryDirectory() as other_dir:
            target = Path(other_dir) / "fuori.txt"
            target.write_text("non dovrebbe essere leggibile", encoding="utf-8")

            responses = _run_worker(
                [_read_file_plugin("READ_TEST", allowed_path=allowed_dir)],
                [{"intent": "READ_TEST", "parameters": {"path": str(target)}}],
            )

            self.assertFalse(responses[1]["success"])
            self.assertIn("PermissionError", responses[1]["error"])

    def test_two_plugins_sharing_the_worker_do_not_leak_each_others_manifest(self):
        """Il manifest attivo e' impostato per OGNI chiamata dal manifest dell'intent che sta per
        girare (vedi main()) - una skill senza manifest non deve MAI ereditare l'accesso concesso
        a un'altra skill nello stesso worker, nemmeno se invocata subito dopo."""
        with tempfile.TemporaryDirectory() as allowed_dir:
            target = Path(allowed_dir) / "dati.txt"
            target.write_text("solo per WITH_MANIFEST", encoding="utf-8")

            responses = _run_worker(
                [
                    _read_file_plugin("WITH_MANIFEST", allowed_path=allowed_dir),
                    _read_file_plugin("WITHOUT_MANIFEST"),
                ],
                [
                    {"intent": "WITH_MANIFEST", "parameters": {"path": str(target)}},
                    {"intent": "WITHOUT_MANIFEST", "parameters": {"path": str(target)}},
                ],
            )

            self.assertTrue(responses[1]["success"])
            self.assertFalse(responses[2]["success"], "WITHOUT_MANIFEST non deve ereditare l'accesso di WITH_MANIFEST")
            self.assertIn("PermissionError", responses[2]["error"])

    def test_plugin_loading_itself_is_never_gated(self):
        """Il gate si installa SOLO dopo che tutti i plugin sono gia' stati caricati (vedi
        _install_path_gate() in main()): il caricamento del file .py del plugin stesso (che usa
        open() per leggersi) non deve mai essere bloccato."""
        responses = _run_worker([ECHO_PLUGIN], [{"intent": "ECHO_TEST", "parameters": {"text": "ok"}}])
        self.assertTrue(responses[1]["success"])


if __name__ == "__main__":
    unittest.main()
