"""Test unitari per skills/close_window.py: nessuna suite esisteva finora. win32gui/win32con
sono sempre mockati."""
import unittest
from unittest import mock

from skills.close_window import CloseWindowSkill


def _fake_win32_modules(windows: dict = None, foreground_hwnd=None, foreground_title=""):
    windows = windows or {}
    win32gui = mock.MagicMock()
    win32gui.IsWindowVisible.return_value = True
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


if __name__ == "__main__":
    unittest.main()
