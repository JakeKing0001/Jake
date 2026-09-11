"""Test unitari per skills/media_control.py: nessuna suite esisteva finora, nessun bug trovato.
'keyboard' e' importato dentro execute(): mock.patch.dict su sys.modules basta perche' l'import
avviene a ogni chiamata."""
import unittest
from unittest import mock

from skills.media_control import MediaControlSkill


class MediaControlTests(unittest.TestCase):
    def test_missing_action_fails(self):
        result = MediaControlSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_unknown_action_fails(self):
        result = MediaControlSkill().execute({"action": "boh"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_known_action_sends_the_right_media_key(self):
        fake_keyboard = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            result = MediaControlSkill().execute({"action": "next"})
        self.assertTrue(result.success)
        fake_keyboard.send.assert_called_once_with("next track")

    def test_a_keyboard_failure_reports_operation_failed(self):
        fake_keyboard = mock.MagicMock()
        fake_keyboard.send.side_effect = RuntimeError("boom")
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            result = MediaControlSkill().execute({"action": "play_pause"})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
