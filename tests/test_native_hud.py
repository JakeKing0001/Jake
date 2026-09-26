"""F4.8.2: l'HUD nativo e' un processo separato e sorvegliato: riavvio solo dopo un crash, con un tetto,
mai dopo una chiusura voluta; lo shutdown del core lo chiude. Processi finti, nessun HUD reale."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.native_hud import NativeHudSupervisor


class _FakeProcess:
    def __init__(self, exit_code):
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

    def _supervisor(self, exit_codes):
        launches = []
        codes = list(exit_codes)

        def popen(args, cwd=None):
            launches.append(args)
            return _FakeProcess(codes.pop(0) if codes else None)

        supervisor = NativeHudSupervisor(self.exe, "http://127.0.0.1:9999", max_restarts=2, backoff_s=(0.01,), popen=popen)
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
