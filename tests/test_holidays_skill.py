"""Test unitari per skills/holidays.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest
from datetime import date
from unittest import mock

from skills.holidays import GetNextHolidaySkill, _easter_sunday, _italian_holidays


class EasterSundayTests(unittest.TestCase):
    def test_known_easter_dates(self):
        # Date verificabili in modo indipendente (calendario liturgico pubblico).
        self.assertEqual(_easter_sunday(2024), date(2024, 3, 31))
        self.assertEqual(_easter_sunday(2025), date(2025, 4, 20))
        self.assertEqual(_easter_sunday(2026), date(2026, 4, 5))


class ItalianHolidaysTests(unittest.TestCase):
    def test_contains_all_twelve_fixed_and_moving_holidays(self):
        holidays = _italian_holidays(2026)
        self.assertEqual(len(holidays), 12)
        self.assertIn("Natale", holidays.values())
        self.assertIn("Pasquetta", holidays.values())


class GetNextHolidayTests(unittest.TestCase):
    def test_finds_the_closest_upcoming_holiday(self):
        with mock.patch("skills.holidays.date") as fake_date:
            fake_date.today.return_value = date(2026, 12, 24)
            fake_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
            result = GetNextHolidaySkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["name"], "Natale")
        self.assertEqual(result.data["days_until"], 1)

    def test_wraps_into_next_year_after_the_last_holiday(self):
        with mock.patch("skills.holidays.date") as fake_date:
            fake_date.today.return_value = date(2026, 12, 27)
            fake_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
            result = GetNextHolidaySkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["name"], "Capodanno")
        self.assertEqual(result.data["days_until"], (date(2027, 1, 1) - date(2026, 12, 27)).days)


if __name__ == "__main__":
    unittest.main()
