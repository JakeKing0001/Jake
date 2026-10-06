"""Calcolo mostrato sulla Calcolatrice di Windows (skills/calculator_show.py) e radice quadrata in CALCULATE.
Prova reale del 07/10/2026. Nessuna finestra vera: ricerca, messa a fuoco e tastiera finte."""
import unittest

from core.response_formatter import format_skill_result
from skills.calculate import CalculateSkill
from skills.calculator_show import ShowOnCalculatorSkill, expression_keys, simulate_standard


class CalculateSqrtTest(unittest.TestCase):
    def test_square_root_and_safety(self):
        skill = CalculateSkill()
        self.assertEqual(skill.execute({"expression": "sqrt(400)"}).data["result"], 20)
        self.assertEqual(skill.execute({"expression": "√400"}).data["result"], 20)
        for bad in ("__import__('os')", "open(1)", "2**100000000", "1/0"):
            self.assertEqual(skill.execute({"expression": bad}).error, "INVALID_EXPRESSION", bad)

    def test_spoken_answer_reads_words_not_symbols(self):
        result = CalculateSkill().execute({"expression": "sqrt(400)"})
        self.assertEqual(format_skill_result("CALCULATE", result), "la radice quadrata di 400 fa 20.")


class KeysTest(unittest.TestCase):
    def test_keys_for_the_standard_calculator(self):
        self.assertEqual(expression_keys("sqrt(400)"), ["400", "@"])
        self.assertEqual(expression_keys("12*8"), ["12", "*", "8", "="])
        self.assertEqual(expression_keys("9**2"), ["9", "*", "9", "="])
        self.assertIsNone(expression_keys("sqrt(9+7)"))

    def test_immediate_execution_is_simulated(self):
        self.assertEqual(simulate_standard(["2", "+", "3", "*", "4", "="]), 20)   # non 14: niente precedenze
        self.assertEqual(simulate_standard(["16", "@", "+", "2", "="]), 6)


class ShowOnCalculatorTest(unittest.TestCase):
    def _skill(self, focused=True, found=True):
        self.typed = []
        return ShowOnCalculatorSkill(launch=lambda: None, find=lambda: (1, "Calcolatrice") if found else None,
                                     focus=lambda hwnd: focused, type_keys=self.typed.append, wait_s=0.1)

    def test_types_the_operation_and_reports_the_result(self):
        result = self._skill().execute({"expression": "sqrt(400)"})
        self.assertTrue(result.success)
        self.assertEqual(self.typed, [["400", "@"]])
        self.assertEqual(format_skill_result("SHOW_ON_CALCULATOR", result),
                         "Fatto sulla calcolatrice: la radice quadrata di 400 fa 20.")

    def test_precedence_the_calculator_would_get_wrong_types_the_result(self):
        result = self._skill().execute({"expression": "2+3*4"})
        self.assertEqual(self.typed, [["14"]])
        self.assertFalse(result.data["exact"])

    def test_never_types_into_another_window(self):
        result = self._skill(focused=False).execute({"expression": "12*8"})
        self.assertEqual(result.error, "CALCULATOR_NOT_FOCUSED")
        self.assertEqual(self.typed, [])
        self.assertEqual(self._skill(found=False).execute({"expression": "12*8"}).error, "WINDOW_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
