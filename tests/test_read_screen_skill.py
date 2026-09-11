"""Test unitari per skills/read_screen.py: nessuna suite esisteva finora, nessun bug trovato.
core.vision.screen.read_screen_text e' sempre mockato."""
import unittest
from unittest import mock

from skills.read_screen import ReadScreenSkill


class ReadScreenTests(unittest.TestCase):
    def test_a_successful_read_returns_the_text(self):
        with mock.patch("core.vision.screen.read_screen_text", return_value="  ciao mondo  ", create=True):
            result = ReadScreenSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["text"], "ciao mondo")
        self.assertFalse(result.data["truncated"])

    def test_ocr_unavailable_reports_ocr_unavailable(self):
        with mock.patch("core.vision.screen.read_screen_text", return_value=None, create=True):
            result = ReadScreenSkill().execute({})
        self.assertEqual(result.error, "OCR_UNAVAILABLE")

    def test_empty_text_reports_not_found(self):
        with mock.patch("core.vision.screen.read_screen_text", return_value="   ", create=True):
            result = ReadScreenSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_an_exception_reports_operation_failed(self):
        with mock.patch("core.vision.screen.read_screen_text", side_effect=RuntimeError("boom"), create=True):
            result = ReadScreenSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_long_text_is_truncated(self):
        long_text = "a" * (ReadScreenSkill.MAX_CHARS + 50)
        with mock.patch("core.vision.screen.read_screen_text", return_value=long_text, create=True):
            result = ReadScreenSkill().execute({})
        self.assertTrue(result.data["truncated"])
        self.assertEqual(len(result.data["text"]), ReadScreenSkill.MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
