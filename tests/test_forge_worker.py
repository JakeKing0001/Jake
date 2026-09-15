"""Test unitari per core/forge_worker.py (F1.6, worker persistente per skill forgiate): nessuna
suite esisteva finora. Gira normalmente come script standalone in un processo sandboxato separato
(vedi core/sandboxed_skill_worker.py); qui si chiama main() direttamente in-process, con
sys.stdin/sys.stdout rimpiazzati da veri oggetti StringIO (il protocollo a righe JSON e' l'intera
interfaccia del componente, stesso principio gia' usato in tests/test_forge_probe.py per il file
di I/O di quel modulo gemello).

F1.6.5/F1.6.6: main() installa gate su builtins.open/os.open E socket.socket.connect/connect_ex
che NON verrebbero mai disinstallati in un worker vero (gira come processo separato, usa e getta -
vedi core/sandboxed_skill_worker.py). Qui main() gira invece IN PROCESSO con questa stessa suite:
senza ripristinarli esplicitamente dopo ogni chiamata, i gate resterebbero installati per DAVVERO
su questo processo di test, rompendo silenziosamente qualunque open()/connessione di rete
successiva in test completamente estranei (es. qualunque test che parla con un vero server HTTP
locale, come tests/test_companion_server.py). _run_worker() li ripristina sempre, in un finally,
indipendentemente da quale test lo chiami."""
import builtins
import io
import json
import os
import socket
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


def _connect_plugin(intent: str, allowed_hosts: list | None = None) -> str:
    """Skill che tenta una connessione TCP vera a host/porta passati nei parametri - usata per
    esercitare il gate F1.6.6. allowed_hosts=None -> nessun MANIFEST dichiarato (nega per
    default). Il PermissionError del gate e' catturato DENTRO la skill (non lasciato propagare
    al gestore generico di forge_worker.main()) cosi' i test possono distinguere un diniego del
    gate da un fallimento di rete vero."""
    manifest_line = ""
    if allowed_hosts is not None:
        manifest_line = f"MANIFEST = {{'allowed_hosts': {allowed_hosts!r}}}\n"
    return (
        manifest_line
        + "import socket\n"
        "from core.skill_result import SkillResult\n"
        "class ConnectSkill:\n"
        f"    metadata = {{'intent': '{intent}', 'description': 'test', 'parameters': {{}}}}\n"
        "    def execute(self, parameters=None):\n"
        "        parameters = parameters or {}\n"
        "        try:\n"
        "            sock = socket.create_connection((parameters['host'], parameters['port']), timeout=2)\n"
        "            sock.close()\n"
        "            return SkillResult(success=True, data={'connected': True})\n"
        "        except PermissionError as exc:\n"
        "            return SkillResult(success=False, data={}, error=f'DENIED:{exc}')\n"
        "def register(registry):\n"
        f"    registry.register_skill('{intent}', ConnectSkill())\n"
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
            # F1.6.5/F1.6.6: vedi il docstring del modulo - i gate vanno sempre tolti da QUESTO
            # processo di test, mai lasciati installati per davvero.
            builtins.open = forge_worker._real_open
            os.open = forge_worker._real_os_open
            socket.socket.connect = forge_worker._real_socket_connect
            socket.socket.connect_ex = forge_worker._real_socket_connect_ex
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
            socket.socket.connect = forge_worker._real_socket_connect
            socket.socket.connect_ex = forge_worker._real_socket_connect_ex
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


class ManifestHostGateTests(unittest.TestCase):
    """F1.6.6 ("negare rete salvo capability con domini/porte specifici"): stesso principio di
    ManifestPathGateTests sopra, per le connessioni di rete - gate applicativo su
    socket.socket.connect/connect_ex, NON garantito dal kernel (vedi il docstring di
    core/forge_worker.py sul perche'). Un listener TCP vero su 127.0.0.1 (porta scelta dal SO)
    fa da bersaglio "concesso" reale - una connessione negata dal gate non deve mai arrivare a
    toccare la rete per davvero, quindi non serve nemmeno un listener per quel caso."""

    def _real_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        return listener.getsockname()  # (host, port) realmente in ascolto

    def test_a_skill_without_a_manifest_cannot_connect_anywhere(self):
        host, port = self._real_listener()
        responses = _run_worker(
            [_connect_plugin("CONNECT_TEST")],
            [{"intent": "CONNECT_TEST", "parameters": {"host": host, "port": port}}],
        )
        self.assertFalse(responses[1]["success"])
        self.assertIn("DENIED", responses[1]["error"])

    def test_a_skill_can_connect_to_a_host_inside_its_declared_manifest(self):
        host, port = self._real_listener()
        responses = _run_worker(
            [_connect_plugin("CONNECT_TEST", allowed_hosts=[f"{host}:{port}"])],
            [{"intent": "CONNECT_TEST", "parameters": {"host": host, "port": port}}],
        )
        self.assertEqual(responses[1], {"success": True, "data": {"connected": True}, "error": None})

    def test_a_skill_cannot_connect_to_a_host_outside_its_declared_manifest(self):
        host, port = self._real_listener()
        responses = _run_worker(
            # concesso solo un dominio del tutto diverso, mai risolto/contattato qui
            [_connect_plugin("CONNECT_TEST", allowed_hosts=["esempio-non-usato.invalid"])],
            [{"intent": "CONNECT_TEST", "parameters": {"host": host, "port": port}}],
        )
        self.assertFalse(responses[1]["success"])
        self.assertIn("DENIED", responses[1]["error"])

    def test_a_declared_host_without_a_port_allows_any_port_on_that_host(self):
        host, port = self._real_listener()
        responses = _run_worker(
            [_connect_plugin("CONNECT_TEST", allowed_hosts=[host])],  # nessuna porta dichiarata
            [{"intent": "CONNECT_TEST", "parameters": {"host": host, "port": port}}],
        )
        self.assertTrue(responses[1]["success"])

    def test_a_declared_host_with_a_specific_port_denies_other_ports_on_the_same_host(self):
        host, port = self._real_listener()
        wrong_port = port + 1 if port < 65535 else port - 1
        responses = _run_worker(
            [_connect_plugin("CONNECT_TEST", allowed_hosts=[f"{host}:{wrong_port}"])],
            [{"intent": "CONNECT_TEST", "parameters": {"host": host, "port": port}}],
        )
        self.assertFalse(responses[1]["success"])
        self.assertIn("DENIED", responses[1]["error"])


if __name__ == "__main__":
    unittest.main()
