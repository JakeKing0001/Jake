"""Test unitari per skills/mouse_control.py: nessuna suite esisteva finora. pyautogui e' sempre
mockato: un test che lo chiamasse per davvero muoverebbe/cliccherebbe il mouse per davvero sulla
macchina che esegue la suite."""
import unittest
from unittest import mock


class ClickMouseTests(unittest.TestCase):
    def _pyautogui(self):
        pyautogui = mock.MagicMock()
        pyautogui.FailSafeException = Exception
        return pyautogui

    def test_missing_coordinates_fails(self):
        with mock.patch.dict("sys.modules", {"pyautogui": self._pyautogui()}):
            from skills.mouse_control import ClickMouseSkill
            result = ClickMouseSkill().execute({"x": 10})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_default_button_is_a_left_click(self):
        pyautogui = self._pyautogui()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            from skills.mouse_control import ClickMouseSkill
            result = ClickMouseSkill().execute({"x": 100, "y": 200})
        self.assertTrue(result.success)
        pyautogui.click.assert_called_once_with(x=100, y=200, button="left")

    def test_right_click(self):
        pyautogui = self._pyautogui()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            from skills.mouse_control import ClickMouseSkill
            result = ClickMouseSkill().execute({"x": 100, "y": 200, "button": "right"})
        self.assertTrue(result.success)
        pyautogui.click.assert_called_once_with(x=100, y=200, button="right")

    def test_double_click_uses_double_click(self):
        pyautogui = self._pyautogui()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            from skills.mouse_control import ClickMouseSkill
            result = ClickMouseSkill().execute({"x": 100, "y": 200, "button": "double"})
        self.assertTrue(result.success)
        pyautogui.doubleClick.assert_called_once_with(x=100, y=200)
        pyautogui.click.assert_not_called()

    def test_non_numeric_coordinates_fail_gracefully(self):
        pyautogui = self._pyautogui()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            from skills.mouse_control import ClickMouseSkill
            result = ClickMouseSkill().execute({"x": "molto a sinistra", "y": 200})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_a_failure_is_reported_not_raised(self):
        pyautogui = self._pyautogui()
        pyautogui.click.side_effect = Exception("boom")
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            from skills.mouse_control import ClickMouseSkill
            result = ClickMouseSkill().execute({"x": 100, "y": 200})
        self.assertEqual(result.error, "OPERATION_FAILED")


class MoveMouseTests(unittest.TestCase):
    def test_missing_coordinates_fails(self):
        with mock.patch.dict("sys.modules", {"pyautogui": mock.MagicMock()}):
            from skills.mouse_control import MoveMouseSkill
            result = MoveMouseSkill().execute({"x": 10})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_moves_the_cursor(self):
        pyautogui = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            from skills.mouse_control import MoveMouseSkill
            result = MoveMouseSkill().execute({"x": 300, "y": 400})
        self.assertTrue(result.success)
        pyautogui.moveTo.assert_called_once_with(300, 400)

    def test_a_failure_is_reported_not_raised(self):
        pyautogui = mock.MagicMock()
        pyautogui.moveTo.side_effect = Exception("boom")
        with mock.patch.dict("sys.modules", {"pyautogui": pyautogui}):
            from skills.mouse_control import MoveMouseSkill
            result = MoveMouseSkill().execute({"x": 100, "y": 200})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
