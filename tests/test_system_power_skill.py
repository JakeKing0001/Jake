"""Test unitari per skills/system_power.py: nessuna suite esisteva finora, nonostante governi
spegnimento/riavvio/sospensione/blocco del PC (ADMIN in core/risk.py, self-confirming - il gate
centrale NON interviene, la skill e' l'UNICA barriera). subprocess.run e' sempre mockato: un
test che lanciasse per davvero 'shutdown /s' spegnerebbe la macchina che esegue la suite."""
import unittest
from unittest import mock

from skills.system_power import SystemPowerSkill


class MissingOrInvalidActionTests(unittest.TestCase):
    def test_missing_action_fails(self):
        result = SystemPowerSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_unrecognized_action_fails(self):
        result = SystemPowerSkill().execute({"action": "qualcosa_di_strano"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_action_is_case_insensitive(self):
        with mock.patch("sys.platform", "win32"), mock.patch("subprocess.run") as run:
            result = SystemPowerSkill().execute({"action": "LOCK"})
        self.assertTrue(result.success)
        run.assert_called_once()


class ConfirmationGateTests(unittest.TestCase):
    """shutdown/restart sono IRREVERSIBILI (impattano tutto il PC, non solo un'app) e richiedono
    sempre conferma esplicita - sleep/lock sono pienamente reversibili e non la richiedono,
    scelta di design dichiarata nel docstring della skill, non un buco."""

    def test_shutdown_without_confirmation_asks_for_it_and_does_not_touch_the_system(self):
        with mock.patch("subprocess.run") as run:
            result = SystemPowerSkill().execute({"action": "shutdown"})
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(result.data["confirm_parameters"], {"action": "shutdown", "confirmed": True})
        run.assert_not_called()

    def test_restart_without_confirmation_asks_for_it_and_does_not_touch_the_system(self):
        with mock.patch("subprocess.run") as run:
            result = SystemPowerSkill().execute({"action": "restart"})
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        run.assert_not_called()

    def test_sleep_runs_immediately_without_confirmation(self):
        with mock.patch("sys.platform", "win32"), mock.patch("subprocess.run") as run:
            result = SystemPowerSkill().execute({"action": "sleep"})
        self.assertTrue(result.success)
        run.assert_called_once()

    def test_lock_runs_immediately_without_confirmation(self):
        with mock.patch("sys.platform", "win32"), mock.patch("subprocess.run") as run:
            result = SystemPowerSkill().execute({"action": "lock"})
        self.assertTrue(result.success)
        run.assert_called_once()

    def test_confirmed_shutdown_actually_calls_the_shutdown_command(self):
        with mock.patch("sys.platform", "win32"), mock.patch("subprocess.run") as run:
            result = SystemPowerSkill().execute({"action": "shutdown", "confirmed": True})
        self.assertTrue(result.success)
        run.assert_called_once_with(["shutdown", "/s", "/t", "5"], check=True)

    def test_confirmed_restart_actually_calls_the_restart_command(self):
        with mock.patch("sys.platform", "win32"), mock.patch("subprocess.run") as run:
            SystemPowerSkill().execute({"action": "restart", "confirmed": True})
        run.assert_called_once_with(["shutdown", "/r", "/t", "5"], check=True)


class PlatformAndFailureTests(unittest.TestCase):
    def test_non_windows_platform_fails_gracefully_without_calling_subprocess(self):
        with mock.patch("sys.platform", "linux"), mock.patch("subprocess.run") as run:
            result = SystemPowerSkill().execute({"action": "lock"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")
        run.assert_not_called()

    def test_a_subprocess_failure_is_reported_as_operation_failed_not_a_crash(self):
        with mock.patch("sys.platform", "win32"), mock.patch("subprocess.run", side_effect=OSError("boom")):
            result = SystemPowerSkill().execute({"action": "lock"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_a_nonzero_exit_from_check_true_is_reported_as_operation_failed(self):
        import subprocess as subprocess_module
        with mock.patch("sys.platform", "win32"):
            with mock.patch("subprocess.run", side_effect=subprocess_module.CalledProcessError(1, "shutdown")):
                result = SystemPowerSkill().execute({"action": "lock"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
