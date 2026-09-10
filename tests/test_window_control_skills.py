"""Test unitari per skills/window_control.py: nessuna suite esisteva finora. win32gui/win32con
sono sempre mockati."""
import unittest
from unittest import mock

from skills.window_control import FocusWindowSkill, MinimizeWindowSkill


def _fake_win32_modules(windows: dict):
    """windows: {hwnd: titolo}. EnumWindows chiama il callback per ognuno."""
    win32gui = mock.MagicMock()
    win32gui.IsWindowVisible.return_value = True
    win32gui.GetWindowText.side_effect = lambda hwnd: windows[hwnd]

    def enum_windows(callback, extra):
        for hwnd in windows:
            callback(hwnd, extra)

    win32gui.EnumWindows.side_effect = enum_windows
    win32con = mock.MagicMock(SW_RESTORE=9, SW_MINIMIZE=6)
    return win32gui, win32con


class FocusWindowTests(unittest.TestCase):
    def test_missing_title_fails(self):
        result = FocusWindowSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_no_matching_window_reports_window_not_found(self):
        win32gui, win32con = _fake_win32_modules({1: "Blocco note"})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = FocusWindowSkill().execute({"title": "spotify"})
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")

    def test_a_matching_window_is_restored_and_focused(self):
        win32gui, win32con = _fake_win32_modules({1: "Spotify Premium"})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = FocusWindowSkill().execute({"title": "spotify"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["title"], "Spotify Premium")
        win32gui.ShowWindow.assert_called_once_with(1, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow.assert_called_once_with(1)

    def test_a_win32_failure_is_reported_not_raised(self):
        win32gui, win32con = _fake_win32_modules({1: "Spotify"})
        win32gui.SetForegroundWindow.side_effect = Exception("negato")
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = FocusWindowSkill().execute({"title": "spotify"})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_invisible_windows_are_ignored(self):
        win32gui, win32con = _fake_win32_modules({1: "Spotify"})
        win32gui.IsWindowVisible.return_value = False
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = FocusWindowSkill().execute({"title": "spotify"})
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")


class MinimizeWindowTests(unittest.TestCase):
    def test_missing_title_fails(self):
        result = MinimizeWindowSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_matching_window_is_minimized(self):
        win32gui, win32con = _fake_win32_modules({1: "Spotify"})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = MinimizeWindowSkill().execute({"title": "spotify"})
        self.assertTrue(result.success)
        win32gui.ShowWindow.assert_called_once_with(1, win32con.SW_MINIMIZE)

    def test_no_matching_window_reports_window_not_found(self):
        win32gui, win32con = _fake_win32_modules({})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = MinimizeWindowSkill().execute({"title": "spotify"})
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
