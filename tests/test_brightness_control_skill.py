"""Test unitari per skills/brightness_control.py: nessuna suite esisteva finora, nessun bug
trovato. subprocess.run e' sempre mockato (mai una vera chiamata a powershell/WMI)."""
import unittest
from unittest import mock

from skills.brightness_control import SetBrightnessSkill


class SetBrightnessTests(unittest.TestCase):
    def test_missing_level_fails(self):
        result = SetBrightnessSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_valid_level_invokes_powershell(self):
        with mock.patch("subprocess.run") as run:
            result = SetBrightnessSkill().execute({"level": 50})
        self.assertTrue(result.success)
        self.assertEqual(result.data["level"], 50)
        run.assert_called_once()
        self.assertIn("powershell", run.call_args.args[0])

    def test_level_is_clamped_to_zero_and_a_hundred(self):
        with mock.patch("subprocess.run"):
            result = SetBrightnessSkill().execute({"level": 500})
        self.assertEqual(result.data["level"], 100)
        with mock.patch("subprocess.run"):
            result = SetBrightnessSkill().execute({"level": -10})
        self.assertEqual(result.data["level"], 0)

    def test_a_powershell_failure_reports_brightness_unavailable(self):
        with mock.patch("subprocess.run", side_effect=OSError("no powershell")):
            result = SetBrightnessSkill().execute({"level": 50})
        self.assertEqual(result.error, "BRIGHTNESS_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
