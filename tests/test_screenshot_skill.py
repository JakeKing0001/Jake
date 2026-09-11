"""Test unitari per skills/screenshot.py: nessuna suite esisteva finora, nessun bug trovato.
core.vision.screen.capture_screenshot e' sempre mockato."""
import unittest
from unittest import mock

from skills.screenshot import TakeScreenshotSkill


class TakeScreenshotTests(unittest.TestCase):
    def test_a_successful_capture_returns_the_path(self):
        with mock.patch("core.vision.screen.capture_screenshot", return_value="C:\\tmp\\shot.png", create=True):
            result = TakeScreenshotSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["path"], "C:\\tmp\\shot.png")

    def test_a_failed_capture_reports_operation_failed(self):
        with mock.patch("core.vision.screen.capture_screenshot", side_effect=RuntimeError("boom"), create=True):
            result = TakeScreenshotSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
