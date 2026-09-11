"""Test unitari per skills/calculate.py: nessuna suite esisteva finora, nessun bug trovato. La
valutazione e' un sotto-insieme di AST (niente nomi/chiamate/attributi), non un eval() arbitrario:
i test coprono sia il calcolo corretto sia il rifiuto di input pericolosi o malformati."""
import unittest

from skills.calculate import CalculateSkill


class CalculateTests(unittest.TestCase):
    def test_missing_expression_fails(self):
        result = CalculateSkill().execute({})
        self.assertEqual(result.error, "INVALID_EXPRESSION")

    def test_basic_arithmetic(self):
        result = CalculateSkill().execute({"expression": "5*3+2"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 17)

    def test_power_and_parentheses(self):
        result = CalculateSkill().execute({"expression": "(2+3)**2"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 25)

    def test_comma_decimal_separator_is_normalized(self):
        result = CalculateSkill().execute({"expression": "1,5+1,5"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 3.0)

    def test_caret_is_treated_as_power(self):
        result = CalculateSkill().execute({"expression": "2^3"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 8)

    def test_division_by_zero_fails_gracefully(self):
        result = CalculateSkill().execute({"expression": "1/0"})
        self.assertEqual(result.error, "INVALID_EXPRESSION")

    def test_letters_are_rejected(self):
        result = CalculateSkill().execute({"expression": "__import__('os')"})
        self.assertEqual(result.error, "INVALID_EXPRESSION")

    def test_malformed_expression_is_rejected(self):
        result = CalculateSkill().execute({"expression": "5*"})
        self.assertEqual(result.error, "INVALID_EXPRESSION")

    def test_empty_parentheses_are_rejected(self):
        result = CalculateSkill().execute({"expression": "()"})
        self.assertEqual(result.error, "INVALID_EXPRESSION")


if __name__ == "__main__":
    unittest.main()
