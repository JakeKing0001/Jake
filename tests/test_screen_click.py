"""Test unitari per il matching testo-schermo (skills/screen_click.py) e per come CLICK_TEXT usa
il ComputerAgent (v3.7, Computer Use Engine: vedi tests/test_computer_agent.py per il controller
stesso). Nessun vero mouse/schermo."""
import unittest
from unittest import mock

from core.computer_agent import ComputerActionResult
from skills.screen_click import ClickTextSkill, find_text_on_screen

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


class FakeComputerAgent:
    """Doppio finto di ComputerAgent (v3.7): la skill gli delega tutto, quindi basta controllare
    che le passi i parametri giusti e traduca il ComputerActionResult in un SkillResult coerente."""

    def __init__(self, words=WORDS, click_result=None):
        self.words = words
        self.click_result = click_result or ComputerActionResult(success=True, x=0, y=0, matched="", verified=True, change_ratio=0.5)
        self.click_calls = []

    def observe(self):
        return self.words

    def locate_text(self, text, words=None):
        return find_text_on_screen(text, words if words is not None else self.words)

    def click_point(self, x, y, button="left", matched=None):
        self.click_calls.append((x, y, button, matched))
        return self.click_result


class ClickTextSkillTests(unittest.TestCase):
    def test_successful_click_reports_the_verification_signal(self):
        agent = FakeComputerAgent(click_result=ComputerActionResult(
            success=True, x=130, y=210, matched="Accedi", verified=True, change_ratio=0.42,
        ))
        skill = ClickTextSkill(computer_agent=agent)

        result = skill.execute({"text": "Accedi"})

        self.assertTrue(result.success)
        self.assertTrue(result.data["screen_changed"])
        self.assertEqual(result.data["change_ratio"], 0.42)
        self.assertEqual(agent.click_calls, [(130, 210, "left", "Accedi")])

    def test_text_not_found_never_attempts_a_click(self):
        agent = FakeComputerAgent(words=WORDS)
        skill = ClickTextSkill(computer_agent=agent)

        result = skill.execute({"text": "Non c'e'"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")
        self.assertEqual(agent.click_calls, [])

    def test_ocr_unavailable_is_reported(self):
        agent = FakeComputerAgent(words=None)
        skill = ClickTextSkill(computer_agent=agent)

        result = skill.execute({"text": "Accedi"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "OCR_UNAVAILABLE")

    def test_click_failure_is_propagated(self):
        agent = FakeComputerAgent(click_result=ComputerActionResult(success=False, error="OPERATION_FAILED"))
        skill = ClickTextSkill(computer_agent=agent)

        result = skill.execute({"text": "Accedi"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_missing_text_parameter(self):
        skill = ClickTextSkill(computer_agent=FakeComputerAgent())
        result = skill.execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")


if __name__ == "__main__":
    unittest.main()
