"""Test unitari per skills/time.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest

from skills.time import TimeSkill


class TimeTests(unittest.TestCase):
    def test_returns_a_time_in_the_expected_format(self):
        result = TimeSkill().execute({})
        self.assertTrue(result.success)
        self.assertRegex(result.data["time"], r"^\d{2}:\d{2}:\d{2}$")

    def test_ignores_any_parameters_passed(self):
        result = TimeSkill().execute({"whatever": "boh"})
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
