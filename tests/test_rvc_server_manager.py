"""Test unitari per core/voice/rvc_server_manager.py: nessuna suite esisteva finora, nessun bug
trovato. subprocess.Popen/time sempre mockati (mai un vero processo RVC avviato). is_installed()
usa un vero filesystem temporaneo."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.voice.rvc_server_manager import RvcServerManager


class IsInstalledTests(unittest.TestCase):
    def test_all_three_paths_present_is_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv-rvc" / "Scripts").mkdir(parents=True)
            (root / ".venv-rvc" / "Scripts" / "python.exe").write_text("x")
            (root / "core" / "voice").mkdir(parents=True)
            (root / "core" / "voice" / "rvc_server.py").write_text("x")
            (root / "rvc_models" / "jake_the_dog").mkdir(parents=True)
            manager = RvcServerManager("jake_the_dog", project_root=root)
            self.assertTrue(manager.is_installed())

    def test_missing_model_directory_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv-rvc" / "Scripts").mkdir(parents=True)
            (root / ".venv-rvc" / "Scripts" / "python.exe").write_text("x")
            (root / "core" / "voice").mkdir(parents=True)
            (root / "core" / "voice" / "rvc_server.py").write_text("x")
            manager = RvcServerManager("jake_the_dog", project_root=root)
            self.assertFalse(manager.is_installed())

    def test_nothing_installed_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = RvcServerManager("jake_the_dog", project_root=Path(tmp))
            self.assertFalse(manager.is_installed())


class EnsureRunningTests(unittest.TestCase):
    def test_an_already_running_server_is_reused_without_spawning_a_process(self):
        manager = RvcServerManager("jake_the_dog")
        with mock.patch.object(manager.client, "is_available", return_value=True):
            with mock.patch("subprocess.Popen") as popen:
                self.assertTrue(manager.ensure_running())
        popen.assert_not_called()

    def test_a_missing_installation_fails_fast_without_spawning_a_process(self):
        manager = RvcServerManager("jake_the_dog")
        with mock.patch.object(manager.client, "is_available", return_value=False):
            with mock.patch.object(manager, "is_installed", return_value=False):
                with mock.patch("subprocess.Popen") as popen:
                    self.assertFalse(manager.ensure_running())
        popen.assert_not_called()

    def test_spawns_and_waits_until_the_server_becomes_available(self):
        manager = RvcServerManager("jake_the_dog")
        process = mock.MagicMock()
        process.poll.return_value = None
        availability = iter([False, False, True])
        with mock.patch.object(manager.client, "is_available", side_effect=lambda: next(availability)):
            with mock.patch.object(manager, "is_installed", return_value=True):
                with mock.patch("subprocess.Popen", return_value=process) as popen:
                    with mock.patch("time.sleep"):
                        self.assertTrue(manager.ensure_running(timeout=10))
        popen.assert_called_once()

    def test_a_process_that_exits_early_reports_failure(self):
        manager = RvcServerManager("jake_the_dog")
        process = mock.MagicMock()
        process.poll.return_value = 1
        with mock.patch.object(manager.client, "is_available", return_value=False):
            with mock.patch.object(manager, "is_installed", return_value=True):
                with mock.patch("subprocess.Popen", return_value=process):
                    with mock.patch("time.sleep"):
                        self.assertFalse(manager.ensure_running(timeout=10))

    def test_a_timeout_with_no_response_reports_failure(self):
        manager = RvcServerManager("jake_the_dog")
        process = mock.MagicMock()
        process.poll.return_value = None
        times = iter([0, 1, 2, 100])
        with mock.patch.object(manager.client, "is_available", return_value=False):
            with mock.patch.object(manager, "is_installed", return_value=True):
                with mock.patch("subprocess.Popen", return_value=process):
                    with mock.patch("time.sleep"):
                        with mock.patch("time.time", side_effect=lambda: next(times)):
                            self.assertFalse(manager.ensure_running(timeout=5))


class StopTests(unittest.TestCase):
    def test_stop_with_no_process_is_a_no_op(self):
        RvcServerManager("jake_the_dog").stop()

    def test_stop_terminates_a_running_process(self):
        manager = RvcServerManager("jake_the_dog")
        process = mock.MagicMock()
        process.poll.return_value = None
        manager._process = process
        manager.stop()
        process.terminate.assert_called_once()
        self.assertIsNone(manager._process)

    def test_stop_kills_a_process_that_does_not_terminate_in_time(self):
        import subprocess
        manager = RvcServerManager("jake_the_dog")
        process = mock.MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=5)
        manager._process = process
        manager.stop()
        process.kill.assert_called_once()

    def test_stop_on_an_already_exited_process_does_not_terminate(self):
        manager = RvcServerManager("jake_the_dog")
        process = mock.MagicMock()
        process.poll.return_value = 0
        manager._process = process
        manager.stop()
        process.terminate.assert_not_called()

class PrewarmTests(unittest.TestCase):
    def test_prewarm_calls_ensure_running_in_background(self):
        manager = RvcServerManager("jake_the_dog")

        with mock.patch.object(
            manager,
            "ensure_running",
            return_value=True,
        ) as ensure:
            manager.prewarm()

            manager._prewarm_thread.join(timeout=2)

        ensure.assert_called_once()

    def test_two_prewarm_calls_do_not_start_two_threads(self):
        manager = RvcServerManager("jake_the_dog")

        gate = __import__("threading").Event()

        def wait():
            gate.wait(1)
            return True

        with mock.patch.object(
            manager,
            "ensure_running",
            side_effect=wait,
        ) as ensure:
            manager.prewarm()
            first = manager._prewarm_thread

            manager.prewarm()
            second = manager._prewarm_thread

            self.assertIs(first, second)

            gate.set()
            first.join(timeout=2)

        ensure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
