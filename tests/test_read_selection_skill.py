"""Test unitari per skills/read_selection.py: nessuna suite esisteva finora. keyboard/
win32clipboard sono importati dentro le funzioni: mock.patch.dict su sys.modules per keyboard,
skills.read_selection._read_clipboard mockato direttamente per il resto. time.sleep sempre
mockato (mai un'attesa reale).

F1: buco reale corretto in questa sessione - la condizione che doveva rilevare "Ctrl+C non ha
copiato nulla di nuovo" (confronto con il contenuto degli appunti PRIMA della pressione) aveva
un "and not text.strip()" di troppo, che la rendeva sempre falsa (gia' coperta dalla clausola
precedente): quando l'utente chiedeva di leggere una selezione inesistente, Jake leggeva ad alta
voce il contenuto VECCHIO gia' presente negli appunti, invece di dire che non c'era nulla di
selezionato."""
import unittest
from unittest import mock

from skills.read_selection import ReadSelectionSkill


def _run(before_after, keyboard_send_side_effect=None):
    fake_keyboard = mock.MagicMock()
    if keyboard_send_side_effect is not None:
        fake_keyboard.send.side_effect = keyboard_send_side_effect
    values = iter(before_after)
    with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
        with mock.patch("skills.read_selection._read_clipboard", side_effect=lambda: next(values)):
            with mock.patch("time.sleep"):
                return ReadSelectionSkill().execute({})


class ReadSelectionTests(unittest.TestCase):
    def test_a_genuinely_new_selection_is_read(self):
        result = _run([None, "testo appena selezionato"])
        self.assertTrue(result.success)
        self.assertEqual(result.data["text"], "testo appena selezionato")

    def test_a_changed_selection_is_read(self):
        result = _run(["vecchio", "nuovo testo selezionato"])
        self.assertTrue(result.success)
        self.assertEqual(result.data["text"], "nuovo testo selezionato")

    def test_an_unchanged_clipboard_reports_no_selection(self):
        """Il buco reale trovato e corretto in questa sessione."""
        result = _run(["vecchio testo negli appunti", "vecchio testo negli appunti"])
        self.assertEqual(result.error, "NO_SELECTION")

    def test_an_empty_clipboard_after_copy_reports_no_selection(self):
        result = _run([None, ""])
        self.assertEqual(result.error, "NO_SELECTION")

    def test_a_ctrl_c_failure_reports_operation_failed(self):
        result = _run([None, None], keyboard_send_side_effect=RuntimeError("boom"))
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_long_text_is_truncated(self):
        long_text = "a" * 2500
        result = _run([None, long_text])
        self.assertTrue(result.data["truncated"])
        self.assertEqual(len(result.data["text"]), 2000)


if __name__ == "__main__":
    unittest.main()
