"""Test unitari per core/gui/hud/win_effects.py: nessuna suite esisteva finora, nessun bug
trovato. ctypes.windll e' sempre mockato: queste funzioni scrivono stato di finestra nativo
Windows (blur, stile esteso, angoli DWM) reale e visibile, non c'e' un modo sicuro di
verificarle contro un vero HWND senza rischiare effetti collaterali visibili sullo schermo
dell'utente durante la suite."""
import unittest
from unittest import mock

from core.gui.hud import win_effects


def _fake_windll(**overrides):
    fake = mock.MagicMock()
    for name, value in overrides.items():
        parts = name.split(".")
        target = fake
        for part in parts[:-1]:
            target = getattr(target, part)
        setattr(target, parts[-1], value)
    return fake


class RgbaToAbgrTests(unittest.TestCase):
    def test_packs_components_in_abgr_order(self):
        result = win_effects.rgba_to_abgr(0x11, 0x22, 0x33, 0x44)
        self.assertEqual(result, 0x44332211)


class NotWindowsTests(unittest.TestCase):
    def test_every_public_function_short_circuits_off_windows(self):
        with mock.patch("sys.platform", "linux"):
            self.assertFalse(win_effects.apply_acrylic(123))
            self.assertFalse(win_effects.set_rounded_corners(123))
            self.assertFalse(win_effects.set_click_through(123, True))
            self.assertFalse(win_effects.set_no_activate(123, True))


class ApplyAcrylicTests(unittest.TestCase):
    def test_a_successful_call_returns_true(self):
        fake_windll = _fake_windll(**{"user32.SetWindowCompositionAttribute.return_value": 1})
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertTrue(win_effects.apply_acrylic(123))

    def test_the_os_call_reports_failure(self):
        fake_windll = _fake_windll(**{"user32.SetWindowCompositionAttribute.return_value": 0})
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertFalse(win_effects.apply_acrylic(123))

    def test_an_exception_is_swallowed(self):
        fake_windll = mock.MagicMock()
        fake_windll.user32.SetWindowCompositionAttribute.side_effect = OSError("boom")
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertFalse(win_effects.apply_acrylic(123))

    def test_disabled_still_issues_the_call(self):
        fake_windll = _fake_windll(**{"user32.SetWindowCompositionAttribute.return_value": 1})
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertTrue(win_effects.apply_acrylic(123, enabled=False))
        fake_windll.user32.SetWindowCompositionAttribute.assert_called_once()


class SetRoundedCornersTests(unittest.TestCase):
    def test_a_successful_call_returns_true(self):
        fake_windll = mock.MagicMock()
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertTrue(win_effects.set_rounded_corners(123))
        self.assertEqual(fake_windll.dwmapi.DwmSetWindowAttribute.call_count, 2)

    def test_an_exception_is_swallowed(self):
        fake_windll = mock.MagicMock()
        fake_windll.dwmapi.DwmSetWindowAttribute.side_effect = OSError("dwmapi mancante")
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertFalse(win_effects.set_rounded_corners(123))


class ClickThroughAndNoActivateTests(unittest.TestCase):
    def _fake_windll_with_style(self, current_style: int):
        fake_windll = mock.MagicMock()
        fake_windll.user32.GetWindowLongPtrW.return_value = current_style
        return fake_windll

    def test_enabling_click_through_sets_the_transparent_and_layered_bits(self):
        fake_windll = self._fake_windll_with_style(0)
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertTrue(win_effects.set_click_through(123, True))
        new_style = fake_windll.user32.SetWindowLongPtrW.call_args.args[2]
        self.assertTrue(new_style & win_effects.WS_EX_TRANSPARENT)
        self.assertTrue(new_style & win_effects.WS_EX_LAYERED)

    def test_disabling_click_through_clears_the_transparent_bit(self):
        fake_windll = self._fake_windll_with_style(win_effects.WS_EX_TRANSPARENT | win_effects.WS_EX_LAYERED)
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertTrue(win_effects.set_click_through(123, False))
        new_style = fake_windll.user32.SetWindowLongPtrW.call_args.args[2]
        self.assertFalse(new_style & win_effects.WS_EX_TRANSPARENT)

    def test_enabling_no_activate_sets_the_noactivate_and_toolwindow_bits(self):
        fake_windll = self._fake_windll_with_style(0)
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertTrue(win_effects.set_no_activate(123, True))
        new_style = fake_windll.user32.SetWindowLongPtrW.call_args.args[2]
        self.assertTrue(new_style & win_effects.WS_EX_NOACTIVATE)
        self.assertTrue(new_style & win_effects.WS_EX_TOOLWINDOW)

    def test_disabling_no_activate_clears_only_the_noactivate_bit(self):
        fake_windll = self._fake_windll_with_style(win_effects.WS_EX_NOACTIVATE | win_effects.WS_EX_TOOLWINDOW)
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            win_effects.set_no_activate(123, False)
        new_style = fake_windll.user32.SetWindowLongPtrW.call_args.args[2]
        self.assertFalse(new_style & win_effects.WS_EX_NOACTIVATE)
        self.assertTrue(new_style & win_effects.WS_EX_TOOLWINDOW)

    def test_an_exception_is_swallowed(self):
        fake_windll = mock.MagicMock()
        fake_windll.user32.GetWindowLongPtrW.side_effect = OSError("hwnd invalido")
        with mock.patch("sys.platform", "win32"), mock.patch("ctypes.windll", fake_windll):
            self.assertFalse(win_effects.set_click_through(123, True))
            self.assertFalse(win_effects.set_no_activate(123, True))


if __name__ == "__main__":
    unittest.main()
