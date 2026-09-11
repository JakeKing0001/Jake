"""Test unitari per skills/active_window.py: nessuna suite esisteva finora, nessun bug trovato.
core.vision.screen.get_active_window_title e' sempre mockato."""
import unittest
from unittest import mock

from skills.active_window import GetActiveWindowSkill


class GetActiveWindowTests(unittest.TestCase):
    def test_a_successful_lookup_returns_the_title(self):
        with mock.patch("core.vision.screen.get_active_window_title", return_value="Blocco note", create=True):
            result = GetActiveWindowSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["title"], "Blocco note")

    def test_an_empty_title_reports_not_found(self):
        with mock.patch("core.vision.screen.get_active_window_title", return_value="", create=True):
            result = GetActiveWindowSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_an_exception_reports_operation_failed(self):
        with mock.patch("core.vision.screen.get_active_window_title", side_effect=RuntimeError("boom"), create=True):
            result = GetActiveWindowSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
