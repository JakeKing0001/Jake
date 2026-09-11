"""Test unitari per skills/fun2.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest
from unittest import mock

from skills.fun2 import (
    FACTS,
    MAGIC_8_BALL_ANSWERS,
    QUOTES,
    ChooseRandomSkill,
    Magic8BallSkill,
    RandomFactSkill,
    RandomQuoteSkill,
    RockPaperScissorsSkill,
)


class RandomQuoteTests(unittest.TestCase):
    def test_returns_a_known_quote(self):
        result = RandomQuoteSkill().execute({})
        self.assertIn(result.data["quote"], QUOTES)


class RandomFactTests(unittest.TestCase):
    def test_returns_a_known_fact(self):
        result = RandomFactSkill().execute({})
        self.assertIn(result.data["fact"], FACTS)


class Magic8BallTests(unittest.TestCase):
    def test_returns_a_known_answer(self):
        result = Magic8BallSkill().execute({"question": "Andra' bene?"})
        self.assertIn(result.data["answer"], MAGIC_8_BALL_ANSWERS)


class ChooseRandomTests(unittest.TestCase):
    def test_missing_options_fails(self):
        result = ChooseRandomSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_single_option_fails(self):
        result = ChooseRandomSkill().execute({"options": ["pizza"]})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_picks_one_of_the_given_options(self):
        result = ChooseRandomSkill().execute({"options": ["pizza", "sushi"]})
        self.assertTrue(result.success)
        self.assertIn(result.data["choice"], ["pizza", "sushi"])


class RockPaperScissorsTests(unittest.TestCase):
    def test_missing_choice_fails(self):
        result = RockPaperScissorsSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_invalid_choice_fails(self):
        result = RockPaperScissorsSkill().execute({"choice": "lucertola"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_tie(self):
        with mock.patch("random.choice", return_value="sasso"):
            result = RockPaperScissorsSkill().execute({"choice": "sasso"})
        self.assertEqual(result.data["outcome"], "pareggio")

    def test_jake_wins(self):
        with mock.patch("random.choice", return_value="sasso"):
            result = RockPaperScissorsSkill().execute({"choice": "forbice"})
        self.assertEqual(result.data["outcome"], "vince Jake")

    def test_user_wins(self):
        with mock.patch("random.choice", return_value="sasso"):
            result = RockPaperScissorsSkill().execute({"choice": "carta"})
        self.assertEqual(result.data["outcome"], "vince l'utente")


if __name__ == "__main__":
    unittest.main()
