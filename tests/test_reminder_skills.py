"""Test unitari per skills/reminder.py (SET_REMINDER/LIST_REMINDERS): nessuna suite esisteva
finora. Usa un ReminderManager vero su file temporaneo."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.reminder_manager import ReminderManager
from skills.reminder import ListRemindersSkill, SetReminderSkill


class _WithManager(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_reminder_skill_test_"))
        self.manager = ReminderManager(db_path=tmp_dir / "reminders.db")

    def tearDown(self):
        self.manager.close()


class SetReminderTests(_WithManager):
    def test_missing_parameters_fails(self):
        result = SetReminderSkill(self.manager).execute({"text": "prendi la medicina"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_in_minutes_creates_a_real_reminder_due_in_the_future(self):
        result = SetReminderSkill(self.manager).execute({"text": "prendi la medicina", "in_minutes": 10})
        self.assertTrue(result.success)
        [reminder] = self.manager.list_upcoming()
        self.assertEqual(reminder["text"], "prendi la medicina")
        due = datetime.fromisoformat(reminder["due_at"])
        self.assertGreater(due, datetime.now(timezone.utc) + timedelta(minutes=9))

    def test_at_time_in_the_future_today_schedules_for_today(self):
        future = (datetime.now().astimezone() + timedelta(hours=2)).strftime("%H:%M")
        result = SetReminderSkill(self.manager).execute({"text": "chiamata", "at_time": future})
        self.assertTrue(result.success)
        self.assertEqual(len(self.manager.list_upcoming()), 1)

    def test_at_time_already_passed_today_schedules_for_tomorrow(self):
        past = (datetime.now().astimezone() - timedelta(hours=1)).strftime("%H:%M")
        SetReminderSkill(self.manager).execute({"text": "chiamata", "at_time": past})
        [reminder] = self.manager.list_upcoming()
        due_local = datetime.fromisoformat(reminder["due_at"]).astimezone()
        self.assertGreater(due_local, datetime.now().astimezone())

    def test_invalid_at_time_fails_gracefully(self):
        result = SetReminderSkill(self.manager).execute({"text": "chiamata", "at_time": "non un orario"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "INVALID_TIME")


class ListRemindersTests(_WithManager):
    def test_no_reminders_reports_not_found(self):
        result = ListRemindersSkill(self.manager).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_upcoming_reminders_in_local_time(self):
        self.manager.add("prendi la medicina", datetime.now(timezone.utc) + timedelta(minutes=30))
        result = ListRemindersSkill(self.manager).execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["reminders"][0]["text"], "prendi la medicina")
        self.assertRegex(result.data["reminders"][0]["due_at_local"], r"\d{2}/\d{2} \d{2}:\d{2}")

    def test_fired_reminders_are_not_listed(self):
        self.manager.add("scaduto", datetime.now(timezone.utc) - timedelta(minutes=1))
        self.manager.due_reminders()  # lo marca come 'fired'
        result = ListRemindersSkill(self.manager).execute({})
        self.assertEqual(result.error, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
