"""Test unitari per skills/math_utils2.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest
from unittest import mock

from skills.math_utils2 import CalculatePercentageSkill, FibonacciSkill, GcdLcmSkill, IsPrimeSkill, RandomNumberSkill


class IsPrimeTests(unittest.TestCase):
    def test_missing_number_fails(self):
        result = IsPrimeSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_prime_number(self):
        result = IsPrimeSkill().execute({"number": 17})
        self.assertTrue(result.success)
        self.assertTrue(result.data["is_prime"])

    def test_a_non_prime_number(self):
        result = IsPrimeSkill().execute({"number": 15})
        self.assertTrue(result.success)
        self.assertFalse(result.data["is_prime"])

    def test_numbers_below_two_are_not_prime(self):
        for number in (0, 1, -5):
            result = IsPrimeSkill().execute({"number": number})
            self.assertFalse(result.data["is_prime"], msg=number)


class FibonacciTests(unittest.TestCase):
    def test_missing_n_fails(self):
        result = FibonacciSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_n_above_max_fails(self):
        result = FibonacciSkill().execute({"n": FibonacciSkill.MAX_N + 1})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_known_values(self):
        result = FibonacciSkill().execute({"n": 10})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 55)

    def test_n_zero(self):
        result = FibonacciSkill().execute({"n": 0})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 0)


class GcdLcmTests(unittest.TestCase):
    def test_missing_parameters_fails(self):
        result = GcdLcmSkill().execute({"a": 4})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_zero_is_rejected(self):
        result = GcdLcmSkill().execute({"a": 0, "b": 4})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_known_values(self):
        result = GcdLcmSkill().execute({"a": 12, "b": 18})
        self.assertTrue(result.success)
        self.assertEqual(result.data["gcd"], 6)
        self.assertEqual(result.data["lcm"], 36)


class CalculatePercentageTests(unittest.TestCase):
    def test_missing_parameters_fails(self):
        result = CalculatePercentageSkill().execute({"percent": 20})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_known_value(self):
        result = CalculatePercentageSkill().execute({"percent": 20, "value": 150})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 30.0)


class RandomNumberTests(unittest.TestCase):
    def test_defaults_to_one_hundred_range(self):
        with mock.patch("random.randint", return_value=42) as randint:
            result = RandomNumberSkill().execute({})
        randint.assert_called_once_with(1, 100)
        self.assertEqual(result.data["result"], 42)

    def test_swaps_min_and_max_when_reversed(self):
        with mock.patch("random.randint", return_value=7) as randint:
            RandomNumberSkill().execute({"min": 100, "max": 1})
        randint.assert_called_once_with(1, 100)

    def test_non_integer_bounds_fall_back_to_defaults(self):
        with mock.patch("random.randint", return_value=7) as randint:
            RandomNumberSkill().execute({"min": "boh", "max": None})
        randint.assert_called_once_with(1, 100)


if __name__ == "__main__":
    unittest.main()
