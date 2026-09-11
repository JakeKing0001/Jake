"""Test unitari per skills/fun.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest
from unittest import mock

from skills.fun import FlipCoinSkill, JOKES, RollDiceSkill, TellJokeSkill


class TellJokeTests(unittest.TestCase):
    def test_returns_one_of_the_known_jokes(self):
        result = TellJokeSkill().execute({})
        self.assertTrue(result.success)
        self.assertIn(result.data["joke"], JOKES)


class RollDiceTests(unittest.TestCase):
    def test_defaults_to_a_single_six_sided_die(self):
        with mock.patch("random.randint", return_value=4):
            result = RollDiceSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["sides"], 6)
        self.assertEqual(result.data["rolls"], [4])
        self.assertEqual(result.data["total"], 4)

    def test_sides_and_count_are_clamped_to_sane_bounds(self):
        result = RollDiceSkill().execute({"sides": 999999, "count": 999})
        self.assertEqual(result.data["sides"], 1000)
        self.assertEqual(len(result.data["rolls"]), 20)

    def test_zero_or_negative_count_falls_back_to_one(self):
        result = RollDiceSkill().execute({"count": 0})
        self.assertEqual(len(result.data["rolls"]), 1)


class FlipCoinTests(unittest.TestCase):
    def test_returns_heads_or_tails(self):
        result = FlipCoinSkill().execute({})
        self.assertTrue(result.success)
        self.assertIn(result.data["result"], ["testa", "croce"])


if __name__ == "__main__":
    unittest.main()
