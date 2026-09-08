"""Test unitari per il click "a vista" (skills/screen_click.py) e il nuovo segnale di
cambiamento visivo dopo il click (v3.6, Vision 2.0). Nessun vero mouse/schermo: pyautogui e le
funzioni di cattura schermo sono mockate."""
import unittest
from unittest import mock

from PIL import Image

from skills.screen_click import ClickTextSkill, _click_and_measure, find_text_on_screen

WORDS = [
    {"text": "Accedi", "line": 0, "x": 100, "y": 200, "w": 60, "h": 20},
    {"text": "Salva", "line": 1, "x": 10, "y": 240, "w": 50, "h": 20},
    {"text": "con", "line": 1, "x": 65, "y": 240, "w": 30, "h": 20},
    {"text": "nome", "line": 1, "x": 100, "y": 240, "w": 45, "h": 20},
]


class FindTextOnScreenTests(unittest.TestCase):
    def test_exact_single_word_match(self):
        hit = find_text_on_screen("Accedi", WORDS)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["matched"], "Accedi")
        self.assertEqual(hit["x"], 100 + 60 // 2)

    def test_multi_word_contiguous_match(self):
        hit = find_text_on_screen("Salva con nome", WORDS)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["matched"], "Salva con nome")

    def test_no_match_returns_none(self):
        self.assertIsNone(find_text_on_screen("Annulla", WORDS))

    def test_empty_inputs_return_none(self):
        self.assertIsNone(find_text_on_screen("", WORDS))
        self.assertIsNone(find_text_on_screen("Accedi", []))


class ClickAndMeasureTests(unittest.TestCase):
    def test_reports_visible_change(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click") as click, mock.patch("time.sleep"):
            clicked, extra = _click_and_measure(5, 5)
        click.assert_called_once_with(5, 5, button="left")
        self.assertTrue(clicked)
        self.assertTrue(extra["screen_changed"])
        self.assertGreater(extra["change_ratio"], 0.9)

    def test_reports_no_visible_change(self):
        same = Image.new("RGB", (10, 10), (0, 0, 0))
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[same, same.copy()]), \
             mock.patch("pyautogui.click"), mock.patch("time.sleep"):
            clicked, extra = _click_and_measure(5, 5)
        self.assertTrue(clicked)
        self.assertFalse(extra["screen_changed"])

    def test_pyautogui_failure_is_reported_but_never_raises(self):
        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (10, 10))), \
             mock.patch("pyautogui.click", side_effect=RuntimeError("no display")):
            clicked, extra = _click_and_measure(5, 5)
        self.assertFalse(clicked)
        self.assertEqual(extra, {})

    def test_screenshot_failure_still_allows_the_click_to_succeed(self):
        """Il segnale di cambiamento visivo e' un extra: se la cattura fallisce (permessi,
        ambiente senza schermo) il click deve comunque essere riportato come riuscito, solo
        senza screen_changed/change_ratio nei dati."""
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=RuntimeError("no screen")), \
             mock.patch("pyautogui.click") as click:
            clicked, extra = _click_and_measure(5, 5)
        click.assert_called_once()
        self.assertTrue(clicked)
        self.assertEqual(extra, {})


class ClickTextSkillTests(unittest.TestCase):
    def test_successful_click_includes_change_signal(self):
        skill = ClickTextSkill()
        with mock.patch("core.vision.screen.read_screen_words", return_value=WORDS), \
             mock.patch("skills.screen_click._click_and_measure", return_value=(True, {"screen_changed": True, "change_ratio": 0.5})):
            result = skill.execute({"text": "Accedi"})
        self.assertTrue(result.success)
        self.assertTrue(result.data["screen_changed"])

    def test_text_not_found_never_attempts_a_click(self):
        skill = ClickTextSkill()
        with mock.patch("core.vision.screen.read_screen_words", return_value=WORDS), \
             mock.patch("skills.screen_click._click_and_measure") as click_and_measure:
            result = skill.execute({"text": "Non c'e'"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")
        click_and_measure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
