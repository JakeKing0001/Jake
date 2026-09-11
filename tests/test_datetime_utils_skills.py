"""Test unitari per skills/datetime_utils.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest
from datetime import date

from skills.datetime_utils import (
    ConvertTimezoneSkill,
    DaysUntilSkill,
    GetDayOfWeekSkill,
    GetWeekNumberSkill,
    parse_spoken_date,
)


class ParseSpokenDateTests(unittest.TestCase):
    def test_empty_input_returns_none(self):
        self.assertIsNone(parse_spoken_date(""))

    def test_relative_words(self):
        today = date.today()
        self.assertEqual(parse_spoken_date("oggi"), today)

    def test_explicit_date_formats(self):
        self.assertEqual(parse_spoken_date("25/12/2026"), date(2026, 12, 25))
        self.assertEqual(parse_spoken_date("2026-12-25"), date(2026, 12, 25))

    def test_a_holiday_name(self):
        result = parse_spoken_date("natale")
        self.assertEqual((result.day, result.month), (25, 12))

    def test_unrecognized_text_returns_none(self):
        self.assertIsNone(parse_spoken_date("qualcosa di incomprensibile"))

    def test_partial_date_uses_current_year_or_next_when_future_requested(self):
        result = parse_spoken_date("01/01", future=True)
        self.assertEqual((result.day, result.month), (1, 1))


class GetDayOfWeekTests(unittest.TestCase):
    def test_defaults_to_today_when_no_date_given(self):
        result = GetDayOfWeekSkill().execute({})
        self.assertTrue(result.success)

    def test_invalid_date_fails(self):
        result = GetDayOfWeekSkill().execute({"date": "non una data"})
        self.assertEqual(result.error, "INVALID_DATE")

    def test_known_weekday(self):
        result = GetDayOfWeekSkill().execute({"date": "25/12/2026"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["weekday"], "venerdì")


class DaysUntilTests(unittest.TestCase):
    def test_missing_date_fails(self):
        result = DaysUntilSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_invalid_date_fails(self):
        result = DaysUntilSkill().execute({"date": "non una data"})
        self.assertEqual(result.error, "INVALID_DATE")

    def test_a_future_date(self):
        result = DaysUntilSkill().execute({"date": "31/12/2099"})
        self.assertTrue(result.success)
        self.assertGreater(result.data["days"], 0)


class GetWeekNumberTests(unittest.TestCase):
    def test_defaults_to_today(self):
        result = GetWeekNumberSkill().execute({})
        self.assertTrue(result.success)
        self.assertIn(result.data["week_number"], range(1, 54))

    def test_invalid_date_fails(self):
        result = GetWeekNumberSkill().execute({"date": "non una data"})
        self.assertEqual(result.error, "INVALID_DATE")

    def test_known_week(self):
        result = GetWeekNumberSkill().execute({"date": "01/01/2024"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["week_number"], 1)


class ConvertTimezoneTests(unittest.TestCase):
    def test_missing_parameters_fails(self):
        result = ConvertTimezoneSkill().execute({"time": "12:00", "from_zone": "Italia"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_unknown_timezone_fails(self):
        result = ConvertTimezoneSkill().execute({"time": "12:00", "from_zone": "Italia", "to_zone": "Marte"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_invalid_time_format_fails(self):
        result = ConvertTimezoneSkill().execute({"time": "mezzogiorno", "from_zone": "Italia", "to_zone": "Tokyo"})
        self.assertEqual(result.error, "INVALID_TIME")

    def test_a_successful_conversion(self):
        result = ConvertTimezoneSkill().execute({"time": "12:00", "from_zone": "utc", "to_zone": "utc"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], "12:00")


if __name__ == "__main__":
    unittest.main()
