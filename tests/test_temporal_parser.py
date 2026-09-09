"""Test unitari per core/temporal_parser.py (F5, Memory 2.0 - "linguaggio naturale per il
tempo" in ROADMAP.md, fase F5). now e' sempre iniettato con una data fissa (mercoledi'
19/06/2024, scelta arbitraria) cosi' i test sono deterministici indipendentemente dal giorno
reale in cui girano."""
import unittest
from datetime import datetime, timezone

from core.temporal_parser import parse_relative_range

NOW = datetime(2024, 6, 19, 12, 0, 0, tzinfo=timezone.utc)  # mercoledi'


class UnrecognizedTextTests(unittest.TestCase):
    def test_empty_text_returns_none(self):
        self.assertIsNone(parse_relative_range("", now=NOW))
        self.assertIsNone(parse_relative_range(None, now=NOW))

    def test_unrecognized_expression_returns_none(self):
        self.assertIsNone(parse_relative_range("prima della riunione", now=NOW))
        self.assertIsNone(parse_relative_range("quando lavoravo a NEST", now=NOW))


class SingleDayTests(unittest.TestCase):
    def test_today_is_the_full_current_day(self):
        since, until = parse_relative_range("oggi", now=NOW)
        self.assertEqual(since, "2024-06-19T00:00:00+00:00")
        self.assertEqual(until, "2024-06-20T00:00:00+00:00")

    def test_yesterday(self):
        since, until = parse_relative_range("ieri", now=NOW)
        self.assertEqual(since, "2024-06-18T00:00:00+00:00")
        self.assertEqual(until, "2024-06-19T00:00:00+00:00")

    def test_the_day_before_yesterday_has_two_spellings(self):
        self.assertEqual(parse_relative_range("l'altro ieri", now=NOW), parse_relative_range("avantieri", now=NOW))
        since, _ = parse_relative_range("avantieri", now=NOW)
        self.assertEqual(since, "2024-06-17T00:00:00+00:00")

    def test_tomorrow_and_the_day_after(self):
        since, until = parse_relative_range("domani", now=NOW)
        self.assertEqual((since, until), ("2024-06-20T00:00:00+00:00", "2024-06-21T00:00:00+00:00"))
        since, until = parse_relative_range("dopodomani", now=NOW)
        self.assertEqual((since, until), ("2024-06-21T00:00:00+00:00", "2024-06-22T00:00:00+00:00"))

    def test_case_and_curly_apostrophe_are_normalized(self):
        self.assertEqual(parse_relative_range("IERI", now=NOW), parse_relative_range("ieri", now=NOW))
        self.assertEqual(parse_relative_range("l’altro ieri", now=NOW), parse_relative_range("l'altro ieri", now=NOW))


class RelativeCountTests(unittest.TestCase):
    def test_last_n_days_ends_at_now_not_at_midnight(self):
        since, until = parse_relative_range("ultimi 3 giorni", now=NOW)
        self.assertEqual(since, "2024-06-16T12:00:00+00:00")
        self.assertEqual(until, NOW.isoformat())

    def test_negli_ultimi_variant_is_also_recognized(self):
        self.assertEqual(parse_relative_range("negli ultimi 7 giorni", now=NOW), parse_relative_range("ultimi 7 giorni", now=NOW))

    def test_last_n_hours(self):
        since, until = parse_relative_range("ultime 2 ore", now=NOW)
        self.assertEqual(since, "2024-06-19T10:00:00+00:00")
        self.assertEqual(until, NOW.isoformat())


class WeekMonthYearTests(unittest.TestCase):
    def test_this_week_starts_on_monday(self):
        since, until = parse_relative_range("questa settimana", now=NOW)
        self.assertEqual(since, "2024-06-17T00:00:00+00:00")  # lunedi' di questa settimana
        self.assertEqual(until, NOW.isoformat())

    def test_last_week_is_the_full_previous_monday_to_monday_range(self):
        since, until = parse_relative_range("la settimana scorsa", now=NOW)
        self.assertEqual(since, "2024-06-10T00:00:00+00:00")
        self.assertEqual(until, "2024-06-17T00:00:00+00:00")

    def test_this_month(self):
        since, until = parse_relative_range("questo mese", now=NOW)
        self.assertEqual(since, "2024-06-01T00:00:00+00:00")
        self.assertEqual(until, NOW.isoformat())

    def test_last_month(self):
        since, until = parse_relative_range("il mese scorso", now=NOW)
        self.assertEqual(since, "2024-05-01T00:00:00+00:00")
        self.assertEqual(until, "2024-06-01T00:00:00+00:00")

    def test_last_month_crosses_a_year_boundary(self):
        january = datetime(2024, 1, 15, tzinfo=timezone.utc)
        since, until = parse_relative_range("il mese scorso", now=january)
        self.assertEqual(since, "2023-12-01T00:00:00+00:00")
        self.assertEqual(until, "2024-01-01T00:00:00+00:00")

    def test_this_year(self):
        since, until = parse_relative_range("quest'anno", now=NOW)
        self.assertEqual(since, "2024-01-01T00:00:00+00:00")
        self.assertEqual(until, NOW.isoformat())

    def test_last_year(self):
        since, until = parse_relative_range("l'anno scorso", now=NOW)
        self.assertEqual(since, "2023-01-01T00:00:00+00:00")
        self.assertEqual(until, "2024-01-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
