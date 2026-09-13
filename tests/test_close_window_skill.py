"""Test unitari per skills/close_window.py: nessuna suite esisteva finora. win32gui/win32con
sono sempre mockati."""
import unittest
from unittest import mock

from skills.close_window import CloseWindowSkill


def _fake_win32_modules(windows: dict = None, foreground_hwnd=None, foreground_title="", still_open=False):
    """still_open (F1.3.2): di default la finestra risulta gia' sparita dopo PostMessage (il
    caso comune, gia' verificato dai test esistenti sotto) - passare True simula un programma che
    ignora WM_CLOSE o mostra un dialogo "salvare le modifiche?" che blocca la chiusura vera."""
    windows = windows or {}
    win32gui = mock.MagicMock()
    win32gui.IsWindowVisible.return_value = True
    win32gui.IsWindow.return_value = still_open
    win32gui.GetWindowText.side_effect = lambda hwnd: windows.get(hwnd, foreground_title if hwnd == foreground_hwnd else "")
    win32gui.GetForegroundWindow.return_value = foreground_hwnd

    def enum_windows(callback, extra):
        for hwnd in windows:
            callback(hwnd, extra)

    win32gui.EnumWindows.side_effect = enum_windows
    win32con = mock.MagicMock(WM_CLOSE=0x0010)
    return win32gui, win32con


class CloseWindowByTitleTests(unittest.TestCase):
    def test_a_matching_window_gets_wm_close(self):
        win32gui, win32con = _fake_win32_modules(windows={1: "Spotify"})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({"title": "spotify"})
        self.assertTrue(result.success)
        win32gui.PostMessage.assert_called_once_with(1, win32con.WM_CLOSE, 0, 0)

    def test_no_matching_window_reports_window_not_found(self):
        win32gui, win32con = _fake_win32_modules(windows={})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({"title": "spotify"})
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")

    def test_a_win32_failure_is_reported_not_raised(self):
        win32gui, win32con = _fake_win32_modules(windows={1: "Spotify"})
        win32gui.PostMessage.side_effect = Exception("negato")
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({"title": "spotify"})
        self.assertEqual(result.error, "OPERATION_FAILED")


class CloseActiveWindowTests(unittest.TestCase):
    def test_no_title_closes_the_foreground_window(self):
        win32gui, win32con = _fake_win32_modules(foreground_hwnd=42, foreground_title="Blocco note")
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["title"], "Blocco note")
        win32gui.PostMessage.assert_called_once_with(42, win32con.WM_CLOSE, 0, 0)

    def test_refuses_to_close_jakes_own_window(self):
        """Salvaguardia gia' presente: senza titolo esplicito, Jake non deve chiudere se stesso
        per errore se la sua e' la finestra attiva."""
        win32gui, win32con = _fake_win32_modules(foreground_hwnd=42, foreground_title="Jake - assistente")
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({})
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")
        win32gui.PostMessage.assert_not_called()

    def test_no_foreground_window_reports_window_not_found(self):
        win32gui, win32con = _fake_win32_modules(foreground_hwnd=None)
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({})
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")

    def test_foreground_window_without_a_title_reports_window_not_found(self):
        win32gui, win32con = _fake_win32_modules(foreground_hwnd=42, foreground_title="")
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({})
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")


class VerifiedClosureTests(unittest.TestCase):
    """F1.3.2 ("prove forti per... finestre"): buco reale - PostMessage(WM_CLOSE) e'
    fire-and-forget, non garantisce che la finestra sia davvero sparita (il programma puo'
    ignorarlo, o mostrare un dialogo "salvare le modifiche?" che blocca la chiusura). Prima di
    questa correzione, success=True veniva dichiarato subito dopo l'invio del messaggio, senza
    nessuna verifica del risultato reale."""

    def setUp(self):
        # CLOSE_WAIT_SECONDS reale sarebbe 3s: qui ridotto perche' il test "ancora aperta" deve
        # aspettare per davvero il timeout completo prima di riportare il fallimento.
        patcher = mock.patch.object(CloseWindowSkill, "CLOSE_WAIT_SECONDS", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)
        interval_patcher = mock.patch.object(CloseWindowSkill, "_POLL_INTERVAL_SECONDS", 0.01)
        interval_patcher.start()
        self.addCleanup(interval_patcher.stop)

    def test_a_window_that_actually_closes_reports_success_with_its_hwnd(self):
        win32gui, win32con = _fake_win32_modules(windows={1: "Spotify"}, still_open=False)
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({"title": "spotify"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["hwnd"], 1)

    def test_a_window_that_ignores_wm_close_reports_operation_failed_not_success(self):
        """Il caso che prima di questa correzione veniva riportato come successo: la finestra
        riceve WM_CLOSE ma resta aperta (dialogo di conferma, programma che lo ignora)."""
        win32gui, win32con = _fake_win32_modules(windows={1: "Blocco note"}, still_open=True)
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = CloseWindowSkill().execute({"title": "blocco note"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")
        self.assertEqual(result.data["hwnd"], 1)


if __name__ == "__main__":
    unittest.main()
