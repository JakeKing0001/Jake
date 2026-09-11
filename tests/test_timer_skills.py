"""Test unitari per skills/timer.py: nessuna suite esisteva finora, nessun bug trovato.
reminder_manager e' sempre un MagicMock (il timer riusa lo scheduler dei promemoria, gia'
verificato dalla propria suite)."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from skills.timer import CancelTimerSkill, ListTimersSkill, SetTimerSkill, human_duration


class HumanDurationTests(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(human_duration(5), "5 secondi")

    def test_singular_forms(self):
        self.assertEqual(human_duration(3661), "1 ora e 1 minuto e 1 secondo")

    def test_zero_seconds_still_shows_something(self):
        self.assertEqual(human_duration(0), "0 secondi")

    def test_omits_zero_components_except_when_everything_is_zero(self):
        self.assertEqual(human_duration(120), "2 minuti")


class SetTimerTests(unittest.TestCase):
    def test_missing_duration_fails(self):
        result = SetTimerSkill(mock.MagicMock()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_invalid_duration_fails(self):
        result = SetTimerSkill(mock.MagicMock()).execute({"minutes": "boh"})
        self.assertEqual(result.error, "INVALID_VALUE")

    def test_a_valid_timer_is_scheduled(self):
        manager = mock.MagicMock()
        result = SetTimerSkill(manager).execute({"minutes": 5, "label": "pasta"})
        self.assertTrue(result.success)
        manager.add.assert_called_once()
        args, kwargs = manager.add.call_args
        self.assertEqual(args[0], "pasta")
        self.assertEqual(kwargs.get("kind") or args[2], "timer")
        self.assertEqual(result.data["seconds_total"], 300)


class CancelTimerTests(unittest.TestCase):
    def test_no_matching_timer_fails(self):
        manager = mock.MagicMock()
        manager.delete_matching.return_value = None
        result = CancelTimerSkill(manager).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_a_matching_timer_is_cancelled(self):
        manager = mock.MagicMock()
        manager.delete_matching.return_value = {"text": "pasta"}
        result = CancelTimerSkill(manager).execute({"label": "pasta"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["label"], "pasta")
        manager.delete_matching.assert_called_once_with("pasta", kind="timer")


class ListTimersTests(unittest.TestCase):
    def test_no_timers_fails(self):
        manager = mock.MagicMock()
        manager.list_upcoming.return_value = []
        result = ListTimersSkill(manager).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_remaining_time_for_each_timer(self):
        due = datetime.now(timezone.utc) + timedelta(minutes=2)
        manager = mock.MagicMock()
        manager.list_upcoming.return_value = [{"text": "pasta", "due_at": due.isoformat()}]
        result = ListTimersSkill(manager).execute({})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["timers"]), 1)
        self.assertEqual(result.data["timers"][0]["label"], "pasta")


if __name__ == "__main__":
    unittest.main()
