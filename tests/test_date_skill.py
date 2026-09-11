"""Test unitari per skills/date.py: nessuna suite esisteva finora, nessun bug trovato."""
import re
import unittest

from skills.date import DateSkill


class DateTests(unittest.TestCase):
    def test_returns_a_date_in_the_expected_format(self):
        result = DateSkill().execute({})
        self.assertTrue(result.success)
        self.assertRegex(result.data["date"], r"^\d{2}/\d{2}/\d{4}$")

    def test_ignores_any_parameters_passed(self):
        result = DateSkill().execute({"whatever": "boh"})
        self.assertTrue(result.success)
        self.assertTrue(re.match(r"^\d{2}/\d{2}/\d{4}$", result.data["date"]))


if __name__ == "__main__":
    unittest.main()
