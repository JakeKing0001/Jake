"""Test unitari per skills/describe_screen.py: nessuna suite esisteva finora, nessun bug trovato.
core.vision.screen.capture_screenshot e vision_provider sono sempre mockati."""
import unittest
from unittest import mock

from skills.describe_screen import DescribeScreenSkill


class DescribeScreenTests(unittest.TestCase):
    def test_a_successful_description(self):
        provider = mock.MagicMock()
        provider.describe.return_value = "Una finestra con un editor di testo."
        with mock.patch("core.vision.screen.capture_screenshot", return_value="C:\\tmp\\shot.png", create=True):
            result = DescribeScreenSkill(provider).execute({"question": "cosa vedi?"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["description"], "Una finestra con un editor di testo.")
        provider.describe.assert_called_once_with("C:\\tmp\\shot.png", "cosa vedi?")

    def test_no_question_passes_none(self):
        provider = mock.MagicMock()
        provider.describe.return_value = "Descrizione generale."
        with mock.patch("core.vision.screen.capture_screenshot", return_value="C:\\tmp\\shot.png", create=True):
            DescribeScreenSkill(provider).execute({})
        provider.describe.assert_called_once_with("C:\\tmp\\shot.png", None)

    def test_screenshot_failure_reports_operation_failed(self):
        provider = mock.MagicMock()
        with mock.patch("core.vision.screen.capture_screenshot", side_effect=RuntimeError("boom"), create=True):
            result = DescribeScreenSkill(provider).execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")
        provider.describe.assert_not_called()

    def test_vision_provider_unavailable_reports_vision_unavailable(self):
        provider = mock.MagicMock()
        provider.describe.return_value = None
        with mock.patch("core.vision.screen.capture_screenshot", return_value="C:\\tmp\\shot.png", create=True):
            result = DescribeScreenSkill(provider).execute({})
        self.assertEqual(result.error, "VISION_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
