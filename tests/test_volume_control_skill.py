"""Test unitari per skills/volume_control.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest
from unittest import mock

from skills.volume_control import VolumeControlSkill


class VolumeControlTests(unittest.TestCase):
    def test_missing_action_fails(self):
        result = VolumeControlSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_unknown_action_fails(self):
        result = VolumeControlSkill().execute({"action": "boh"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_known_action_sends_the_right_key(self):
        fake_keyboard = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            result = VolumeControlSkill().execute({"action": "mute"})
        self.assertTrue(result.success)
        fake_keyboard.send.assert_called_once_with("volume mute")

    def test_a_keyboard_failure_reports_operation_failed(self):
        fake_keyboard = mock.MagicMock()
        fake_keyboard.send.side_effect = RuntimeError("boom")
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            result = VolumeControlSkill().execute({"action": "up"})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
