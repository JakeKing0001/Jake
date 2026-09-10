"""Test unitari per skills/keyboard_control.py: nessuna suite esisteva finora. keyboard/
pyautogui sono sempre mockati: un test che li chiamasse per davvero digiterebbe/premerebbe tasti
per davvero sulla macchina che esegue la suite."""
import unittest
from unittest import mock

from skills.keyboard_control import PressKeySkill, TypeTextSkill


class TypeTextTests(unittest.TestCase):
    def test_missing_text_fails(self):
        result = TypeTextSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_text_is_typed_via_keyboard_write(self):
        keyboard = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"keyboard": keyboard}):
            result = TypeTextSkill().execute({"text": "perché"})
        self.assertTrue(result.success)
        keyboard.write.assert_called_once_with("perché", delay=0.005)

    def test_a_failure_is_reported_not_raised(self):
        keyboard = mock.MagicMock()
        keyboard.write.side_effect = Exception("boom")
        with mock.patch.dict("sys.modules", {"keyboard": keyboard}):
            result = TypeTextSkill().execute({"text": "ciao"})
        self.assertEqual(result.error, "OPERATION_FAILED")


class PressKeyTests(unittest.TestCase):
    def test_missing_keys_fails(self):
        with mock.patch.dict("sys.modules", {"pyautogui": mock.MagicMock()}):
            result = PressKeySkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_single_key_uses_press(self):
        pyautogui = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            result = PressKeySkill().execute({"keys": "enter"})
        self.assertTrue(result.success)
        pyautogui.press.assert_called_once_with("enter")
        pyautogui.hotkey.assert_not_called()

    def test_a_combo_uses_hotkey(self):
        pyautogui = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            result = PressKeySkill().execute({"keys": "ctrl+shift+t"})
        self.assertTrue(result.success)
        pyautogui.hotkey.assert_called_once_with("ctrl", "shift", "t")

    def test_italian_key_aliases_are_translated(self):
        pyautogui = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            result = PressKeySkill().execute({"keys": "invio"})
        self.assertTrue(result.success)
        pyautogui.press.assert_called_once_with("enter")

    def test_win_plus_dot_combo(self):
        pyautogui = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            result = PressKeySkill().execute({"keys": "windows+."})
        self.assertTrue(result.success)
        pyautogui.hotkey.assert_called_once_with("win", ".")

    def test_bare_plus_key_is_pressed_literally(self):
        pyautogui = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            result = PressKeySkill().execute({"keys": "+"})
        self.assertTrue(result.success)
        pyautogui.press.assert_called_once_with("+")

    def test_ctrl_plus_the_plus_key_combo(self):
        pyautogui = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            result = PressKeySkill().execute({"keys": "ctrl++"})
        self.assertTrue(result.success)
        pyautogui.hotkey.assert_called_once_with("ctrl", "+")

    def test_a_failure_is_reported_not_raised(self):
        pyautogui = mock.MagicMock()
        pyautogui.press.side_effect = Exception("boom")
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            result = PressKeySkill().execute({"keys": "enter"})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
