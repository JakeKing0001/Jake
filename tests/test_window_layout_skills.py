"""Test unitari per skills/window_layout.py: nessuna suite esisteva finora. win32gui/win32con/
keyboard sono sempre mockati; core.vision.screen.list_open_window_titles e' mockato per
ListOpenWindowsSkill (tocca l'API vera di enumerazione finestre, gia' esercitata altrove)."""
import unittest
from unittest import mock

from skills.window_layout import (
    ListOpenWindowsSkill, MaximizeWindowSkill, MinimizeAllWindowsSkill, ResizeWindowSkill,
    RestoreWindowSkill, SetWindowAlwaysOnTopSkill, SnapWindowLeftSkill, SnapWindowRightSkill,
    SwitchNextWindowSkill,
)


def _fake_win32_modules(windows: dict):
    win32gui = mock.MagicMock()
    win32gui.IsWindowVisible.return_value = True
    win32gui.GetWindowText.side_effect = lambda hwnd: windows[hwnd]

    def enum_windows(callback, extra):
        for hwnd in windows:
            callback(hwnd, extra)

    win32gui.EnumWindows.side_effect = enum_windows
    win32con = mock.MagicMock(
        SW_MAXIMIZE=3, SW_RESTORE=9, HWND_TOPMOST=-1, SWP_NOMOVE=0x2, SWP_NOSIZE=0x1, SWP_NOZORDER=0x4,
    )
    return win32gui, win32con


class ListOpenWindowsTests(unittest.TestCase):
    def test_no_windows_reports_not_found(self):
        with mock.patch("core.vision.screen.list_open_window_titles", return_value=[]):
            result = ListOpenWindowsSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_the_open_window_titles(self):
        with mock.patch("core.vision.screen.list_open_window_titles", return_value=["Spotify", "Blocco note"]):
            result = ListOpenWindowsSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["titles"], ["Spotify", "Blocco note"])


class MaximizeWindowTests(unittest.TestCase):
    def test_missing_title_fails(self):
        result = MaximizeWindowSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_no_match_reports_window_not_found(self):
        win32gui, win32con = _fake_win32_modules({})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = MaximizeWindowSkill().execute({"title": "spotify"})
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")

    def test_a_match_is_maximized(self):
        win32gui, win32con = _fake_win32_modules({1: "Spotify"})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = MaximizeWindowSkill().execute({"title": "spotify"})
        self.assertTrue(result.success)
        win32gui.ShowWindow.assert_called_once_with(1, win32con.SW_MAXIMIZE)


class RestoreWindowTests(unittest.TestCase):
    def test_missing_title_fails(self):
        result = RestoreWindowSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_match_is_restored(self):
        win32gui, win32con = _fake_win32_modules({1: "Spotify"})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = RestoreWindowSkill().execute({"title": "spotify"})
        self.assertTrue(result.success)
        win32gui.ShowWindow.assert_called_once_with(1, win32con.SW_RESTORE)


class KeyboardShortcutSkillsTests(unittest.TestCase):
    def test_snap_left_sends_the_right_shortcut(self):
        keyboard = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": keyboard}):
            result = SnapWindowLeftSkill().execute({})
        self.assertTrue(result.success)
        keyboard.send.assert_called_once_with("windows+left")

    def test_snap_right_sends_the_right_shortcut(self):
        keyboard = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": keyboard}):
            result = SnapWindowRightSkill().execute({})
        self.assertTrue(result.success)
        keyboard.send.assert_called_once_with("windows+right")

    def test_switch_next_window_sends_alt_tab(self):
        keyboard = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": keyboard}):
            result = SwitchNextWindowSkill().execute({})
        self.assertTrue(result.success)
        keyboard.send.assert_called_once_with("alt+tab")

    def test_minimize_all_sends_windows_d(self):
        keyboard = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": keyboard}):
            result = MinimizeAllWindowsSkill().execute({})
        self.assertTrue(result.success)
        keyboard.send.assert_called_once_with("windows+d")


class SetWindowAlwaysOnTopTests(unittest.TestCase):
    def test_missing_title_fails(self):
        result = SetWindowAlwaysOnTopSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_match_is_pinned_on_top(self):
        win32gui, win32con = _fake_win32_modules({1: "Spotify"})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = SetWindowAlwaysOnTopSkill().execute({"title": "spotify"})
        self.assertTrue(result.success)
        win32gui.SetWindowPos.assert_called_once()
        self.assertEqual(win32gui.SetWindowPos.call_args[0][1], win32con.HWND_TOPMOST)


class ResizeWindowTests(unittest.TestCase):
    def test_missing_parameters_fails(self):
        self.assertEqual(ResizeWindowSkill().execute({"title": "spotify"}).error, "MISSING_PARAMETERS")
        self.assertEqual(ResizeWindowSkill().execute({"title": "spotify", "width": 800}).error, "MISSING_PARAMETERS")

    def test_a_match_is_resized_to_the_given_dimensions(self):
        win32gui, win32con = _fake_win32_modules({1: "Spotify"})
        with mock.patch.dict("sys.modules", {"win32gui": win32gui, "win32con": win32con}):
            result = ResizeWindowSkill().execute({"title": "spotify", "width": 800, "height": 600})
        self.assertTrue(result.success)
        self.assertEqual(result.data["width"], 800)
        self.assertEqual(result.data["height"], 600)
        win32gui.SetWindowPos.assert_called_once_with(
            1, None, 0, 0, 800, 600, win32con.SWP_NOMOVE | win32con.SWP_NOZORDER,
        )


if __name__ == "__main__":
    unittest.main()
