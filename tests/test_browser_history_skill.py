"""Test unitari per skills/browser_history.py::GetBrowserHistorySkill: nessuna suite esisteva
finora (esisteva solo tests/test_browser_history.py, che copre il modulo di piu' basso livello
core/browser_history.py::read_recent_history, non questa skill). core.browser_history.
read_recent_history e' sempre mockato.

F1: buco reale corretto in questa sessione (stesso pattern gia' corretto in skills/notes.py e
skills/reminders_extra.py) - 'limit' veniva passato direttamente a int(limit) senza gestire un
valore non numerico: 'quanti siti recenti, tipo boh' avrebbe sollevato un ValueError mai
catturato invece di usare il default."""
import unittest
from unittest import mock

from skills.browser_history import GetBrowserHistorySkill


class GetBrowserHistoryTests(unittest.TestCase):
    def test_a_successful_read_returns_the_entries(self):
        entries = [{"url": "https://example.com", "title": "Example"}]
        with mock.patch("core.browser_history.read_recent_history", return_value=entries) as read:
            result = GetBrowserHistorySkill().execute({"limit": 5})
        self.assertTrue(result.success)
        self.assertEqual(result.data["entries"], entries)
        read.assert_called_once_with(limit=5)

    def test_missing_limit_uses_the_default(self):
        with mock.patch("core.browser_history.read_recent_history", return_value=[{"url": "x"}]) as read:
            GetBrowserHistorySkill().execute({})
        read.assert_called_once_with(limit=GetBrowserHistorySkill.DEFAULT_LIMIT)

    def test_non_numeric_limit_falls_back_to_the_default_instead_of_crashing(self):
        """Il buco reale trovato e corretto in questa sessione."""
        with mock.patch("core.browser_history.read_recent_history", return_value=[{"url": "x"}]) as read:
            result = GetBrowserHistorySkill().execute({"limit": "boh"})
        self.assertTrue(result.success)
        read.assert_called_once_with(limit=GetBrowserHistorySkill.DEFAULT_LIMIT)

    def test_zero_or_negative_limit_falls_back_to_the_default(self):
        with mock.patch("core.browser_history.read_recent_history", return_value=[{"url": "x"}]) as read:
            GetBrowserHistorySkill().execute({"limit": 0})
        read.assert_called_once_with(limit=GetBrowserHistorySkill.DEFAULT_LIMIT)

    def test_history_unavailable_reports_browser_history_unavailable(self):
        with mock.patch("core.browser_history.read_recent_history", return_value=None):
            result = GetBrowserHistorySkill().execute({})
        self.assertEqual(result.error, "BROWSER_HISTORY_UNAVAILABLE")

    def test_no_entries_reports_not_found(self):
        with mock.patch("core.browser_history.read_recent_history", return_value=[]):
            result = GetBrowserHistorySkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
