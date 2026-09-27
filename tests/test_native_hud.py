"""F4.8.2: l'HUD nativo e' un processo separato e sorvegliato: riavvio solo dopo un crash, con un tetto,
mai dopo una chiusura voluta; lo shutdown del core lo chiude. Processi finti, nessun HUD reale."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.native_hud import NativeHudSupervisor


class _FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, exit_code):
        self.stdin = _FakeStdin()
        self.exit_code = exit_code
        self._done = threading.Event()
        self.terminated = False
        if exit_code is not None:
            self._done.set()

    def wait(self, timeout=None):
        if not self._done.wait(timeout if timeout is not None else 5):
            import subprocess
            raise subprocess.TimeoutExpired("hud", timeout)
        return self.exit_code

    def poll(self):
        return self.exit_code if self._done.is_set() else None

    def terminate(self):
        self.terminated = True
        self.exit_code = 1
        self._done.set()

    def kill(self):
        self.terminate()


class NativeHudSupervisorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.exe = Path(tmp.name) / "JakeHud.exe"
        self.exe.write_bytes(b"")

    def _supervisor(self, exit_codes, credentials=None):
        launches = []
        codes = list(exit_codes)
        self.processes = []

        def popen(args, cwd=None, stdin=None):
            launches.append(args)
            process = _FakeProcess(codes.pop(0) if codes else None)
            self.processes.append(process)
            return process

        supervisor = NativeHudSupervisor(self.exe, "http://127.0.0.1:9999", max_restarts=2, backoff_s=(0.01,), popen=popen,
                                         credentials=credentials)
        self.addCleanup(supervisor.stop)
        return supervisor, launches

    @staticmethod
    def _wait(predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not predicate():
            time.sleep(0.01)
        return predicate()

    def test_the_hud_gets_the_real_companion_address(self):
        supervisor, launches = self._supervisor([None])
        self.assertTrue(supervisor.start())
        self.assertEqual(launches[0][1:], ["--jake-url", "http://127.0.0.1:9999"])

    def test_credentials_travel_on_stdin_never_on_the_command_line(self):
        supervisor, launches = self._supervisor([None], credentials={"device_id": "native-hud-local", "token": "segreto"})
        supervisor.start()
        self.assertIn("--credentials-stdin", launches[0])
        self.assertNotIn("segreto", " ".join(launches[0]))
        self.assertEqual(json.loads(self.processes[0].stdin.data), {"device_id": "native-hud-local", "token": "segreto"})
        self.assertTrue(self.processes[0].stdin.closed)

    def test_a_crash_is_restarted_but_only_up_to_the_limit(self):
        supervisor, launches = self._supervisor([3, 3, 3, 3])
        supervisor.start()
        self.assertTrue(self._wait(lambda: supervisor.gave_up))
        self.assertEqual(len(launches), 3, "primo avvio + 2 riavvii, poi basta")

    def test_a_hud_closed_by_the_user_is_not_reopened(self):
        supervisor, launches = self._supervisor([0])
        supervisor.start()
        supervisor._thread.join(2)
        self.assertEqual(len(launches), 1)
        self.assertFalse(supervisor.gave_up)

    def test_stop_closes_a_running_hud_without_restarting_it(self):
        supervisor, launches = self._supervisor([None])
        supervisor.start()
        process = supervisor._process
        supervisor.stop()
        self.assertTrue(process.terminated)
        self.assertEqual(len(launches), 1)

    def test_a_missing_executable_does_not_start_anything(self):
        launches = []
        supervisor = NativeHudSupervisor(self.exe.parent / "manca.exe", "http://x", popen=lambda *a, **k: launches.append(a))
        self.assertFalse(supervisor.start())
        self.assertEqual(launches, [])


if __name__ == "__main__":
    unittest.main()


class NativeHudCredentialTests(unittest.TestCase):
    """Bug reale (26/09/2026, HUD aperto dall'utente): "Host requires authentication" ripetuto. Il core avviava
    JakeHud.exe ma, con l'autenticazione per-dispositivo attiva, ogni richiesta dell'HUD riceveva 401."""

    def setUp(self):
        import types

        from core.companion_guard import CompanionGuard
        from core.companion_server import CompanionServer
        from core.device_credential_store import DeviceCredentialStore
        from core.jake_core import JakeCore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = DeviceCredentialStore(db_path=Path(tmp.name) / "devices.db")
        self.addCleanup(self.store.close)
        self.server = CompanionServer(host="127.0.0.1", port=0, credential_store=self.store, guard=CompanionGuard(audit=None))
        self.server.start()
        self.addCleanup(self.server.stop)
        self.core = JakeCore.__new__(JakeCore)
        self.core.companion_server = self.server
        self.core.logger = types.SimpleNamespace(exception=lambda *a, **k: None, warning=lambda *a, **k: None)
        self.JakeCore = JakeCore

    def _status(self, token=None):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(f"http://127.0.0.1:{self.server.port}/status")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_the_hud_gets_a_scoped_credential_that_the_server_accepts_and_shutdown_revokes(self):
        from core.companion_guard import EndpointClass

        self.assertEqual(self._status(), 401, "senza credenziale resta chiuso: nessuna eccezione per localhost")
        credentials = self.JakeCore._provision_native_hud_credential(self.core)
        self.assertEqual(credentials["device_id"], "native-hud-local")
        self.assertEqual(self._status(credentials["token"]), 200)
        self.assertEqual(self.server.guard.capabilities_of("native-hud-local"),
                         frozenset({EndpointClass.READ_ONLY, EndpointClass.COMMAND}))
        self.JakeCore._revoke_native_hud_credential(self.core)
        self.assertEqual(self._status(credentials["token"]), 401)

    def test_each_core_start_rotates_the_credential(self):
        first = self.JakeCore._provision_native_hud_credential(self.core)
        second = self.JakeCore._provision_native_hud_credential(self.core)
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(self._status(first["token"]), 401)
        self.assertEqual(self._status(second["token"]), 200)
