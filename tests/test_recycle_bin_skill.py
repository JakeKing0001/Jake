"""Test unitari per skills/recycle_bin.py: nessuna suite esisteva finora, nonostante
EMPTY_RECYCLE_BIN sia DESTRUCTIVE (core/risk.py) e self-confirming - il gate centrale non
interviene, la skill e' l'unica barriera. ctypes.windll.shell32.SHEmptyRecycleBinW e' sempre
mockato: un test che lo chiamasse per davvero svuoterebbe il cestino della macchina che esegue
la suite."""
import ctypes
import unittest
from unittest import mock

from skills.recycle_bin import EmptyRecycleBinSkill


class ConfirmationGateTests(unittest.TestCase):
    def test_without_confirmation_asks_for_it_and_does_not_touch_the_recycle_bin(self):
        with mock.patch.object(ctypes.windll.shell32, "SHEmptyRecycleBinW") as api_call:
            result = EmptyRecycleBinSkill().execute({})
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(result.data["confirm_parameters"], {"confirmed": True})
        api_call.assert_not_called()

    def test_confirmed_call_actually_invokes_the_windows_api(self):
        with mock.patch.object(ctypes.windll.shell32, "SHEmptyRecycleBinW", return_value=0) as api_call:
            result = EmptyRecycleBinSkill().execute({"confirmed": True})
        self.assertTrue(result.success)
        api_call.assert_called_once()


class ApiResultHandlingTests(unittest.TestCase):
    def test_success_code_reports_success(self):
        with mock.patch.object(ctypes.windll.shell32, "SHEmptyRecycleBinW", return_value=0):
            result = EmptyRecycleBinSkill().execute({"confirmed": True})
        self.assertTrue(result.success)

    def test_already_empty_code_is_also_treated_as_success(self):
        """0x8000FFFF (come int con segno a 32 bit) capita quando il cestino e' gia' vuoto: il
        cestino risulta comunque vuoto, non e' un errore da riportare all'utente."""
        with mock.patch.object(ctypes.windll.shell32, "SHEmptyRecycleBinW", return_value=-2147418113):
            result = EmptyRecycleBinSkill().execute({"confirmed": True})
        self.assertTrue(result.success)

    def test_any_other_return_code_is_reported_as_a_failure(self):
        with mock.patch.object(ctypes.windll.shell32, "SHEmptyRecycleBinW", return_value=-1):
            result = EmptyRecycleBinSkill().execute({"confirmed": True})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
