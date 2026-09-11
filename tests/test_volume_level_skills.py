"""Test unitari per skills/volume_level.py: nessuna suite esisteva finora, nessun bug trovato.
skills.volume_level._endpoint_volume e' sempre mockato (mai una vera chiamata a pycaw/CoreAudio)."""
import unittest
from unittest import mock

from skills.volume_level import GetVolumeLevelSkill, SetVolumeLevelSkill


class SetVolumeLevelTests(unittest.TestCase):
    def test_missing_level_fails(self):
        result = SetVolumeLevelSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_non_numeric_level_fails(self):
        result = SetVolumeLevelSkill().execute({"level": "boh"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_level_is_clamped_to_zero_and_a_hundred(self):
        endpoint = mock.MagicMock()
        endpoint.GetMute.return_value = 0
        with mock.patch("skills.volume_level._endpoint_volume", return_value=endpoint):
            result = SetVolumeLevelSkill().execute({"level": 500})
        self.assertEqual(result.data["level"], 100)
        endpoint.SetMasterVolumeLevelScalar.assert_called_once_with(1.0, None)

    def test_unmutes_when_setting_a_positive_level_while_muted(self):
        endpoint = mock.MagicMock()
        endpoint.GetMute.return_value = 1
        with mock.patch("skills.volume_level._endpoint_volume", return_value=endpoint):
            result = SetVolumeLevelSkill().execute({"level": 50})
        self.assertTrue(result.success)
        endpoint.SetMute.assert_called_once_with(0, None)

    def test_does_not_unmute_when_setting_level_to_zero(self):
        endpoint = mock.MagicMock()
        endpoint.GetMute.return_value = 1
        with mock.patch("skills.volume_level._endpoint_volume", return_value=endpoint):
            SetVolumeLevelSkill().execute({"level": 0})
        endpoint.SetMute.assert_not_called()

    def test_audio_backend_unavailable_reports_audio_unavailable(self):
        with mock.patch("skills.volume_level._endpoint_volume", side_effect=OSError("no audio")):
            result = SetVolumeLevelSkill().execute({"level": 50})
        self.assertEqual(result.error, "AUDIO_UNAVAILABLE")


class GetVolumeLevelTests(unittest.TestCase):
    def test_a_successful_read_returns_level_and_muted(self):
        endpoint = mock.MagicMock()
        endpoint.GetMasterVolumeLevelScalar.return_value = 0.42
        endpoint.GetMute.return_value = 0
        with mock.patch("skills.volume_level._endpoint_volume", return_value=endpoint):
            result = GetVolumeLevelSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["level"], 42)
        self.assertFalse(result.data["muted"])

    def test_audio_backend_unavailable_reports_audio_unavailable(self):
        with mock.patch("skills.volume_level._endpoint_volume", side_effect=OSError("no audio")):
            result = GetVolumeLevelSkill().execute({})
        self.assertEqual(result.error, "AUDIO_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
