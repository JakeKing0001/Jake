"""Test unitari per core/vision/screen.py::ocr_available (fix di un fallimento reale in CI - vedi
ROADMAP_EXECUTION.md, sezione F3.5.1): il runner CI condiviso di questo progetto (GitHub Actions
windows-latest) non ha un motore OCR disponibile (nessun profilo utente interattivo reale),
verificato empiricamente. Mockato qui con finti deterministici - nessuna dipendenza da un motore
OCR reale, ne' dallo schermo."""
import unittest
from unittest import mock

from core.vision.screen import ocr_available


class OcrAvailableTests(unittest.TestCase):
    def test_true_when_read_screen_words_returns_a_result(self):
        with mock.patch("core.vision.screen.read_screen_words", return_value=[]):
            self.assertTrue(ocr_available())

    def test_false_when_read_screen_words_returns_none(self):
        with mock.patch("core.vision.screen.read_screen_words", return_value=None):
            self.assertFalse(ocr_available())


if __name__ == "__main__":
    unittest.main()
