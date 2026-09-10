"""Test unitari per skills/reminders_extra.py: nessuna suite esisteva finora. Usa un
ReminderManager vero su file temporaneo (stesso motore reale di SET_REMINDER/LIST_REMINDERS).

F1 (indiretto): buco reale trovato e corretto in questa sessione. START_POMODORO faceva
int(minutes) senza catturare il ValueError: un valore non numerico (es. il modello scrive
'trenta' invece di 30) faceva crashare la skill (catturato solo dal gestore generico di
JakeCore.answer(), che nasconde il vero motivo all'utente)."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.reminder_manager import ReminderManager
from skills.reminders_extra import (
    DeleteReminderSkill, SetDailyReminderSkill, SnoozeReminderSkill, StartPomodoroSkill, StopPomodoroSkill,
)


class _WithManager(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_reminders_extra_test_"))
        self.manager = ReminderManager(db_path=tmp_dir / "reminders.db")

    def tearDown(self):
        self.manager.close()


class SnoozeReminderTests(_WithManager):
    def test_missing_text_or_minutes_fails(self):
        self.assertEqual(SnoozeReminderSkill(self.manager).execute({"minutes": 5}).error, "MISSING_PARAMETERS")
        self.assertEqual(SnoozeReminderSkill(self.manager).execute({"text": "medicina"}).error, "MISSING_PARAMETERS")

    def test_non_integer_minutes_fails_instead_of_crashing(self):
        result = SnoozeReminderSkill(self.manager).execute({"text": "medicina", "minutes": "dieci"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_snoozing_an_unknown_reminder_reports_not_found(self):
        result = SnoozeReminderSkill(self.manager).execute({"text": "non esiste", "minutes": 10})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_snoozing_pushes_the_due_date_forward(self):
        self.manager.add("prendi la medicina", datetime.now(timezone.utc) - timedelta(minutes=1))
        result = SnoozeReminderSkill(self.manager).execute({"text": "medicina", "minutes": 15})
        self.assertTrue(result.success)
        self.assertEqual(self.manager.due_reminders(), [])  # non e' piu' scaduto


class DeleteReminderTests(_WithManager):
    def test_missing_text_fails(self):
        result = DeleteReminderSkill(self.manager).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_deleting_an_unknown_reminder_reports_not_found(self):
        result = DeleteReminderSkill(self.manager).execute({"text": "non esiste"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_deleting_a_real_reminder_removes_it(self):
        self.manager.add("prendi la medicina", datetime.now(timezone.utc) + timedelta(minutes=5))
        result = DeleteReminderSkill(self.manager).execute({"text": "medicina"})
        self.assertTrue(result.success)
        self.assertEqual(self.manager.list_upcoming(), [])


class SetDailyReminderTests(_WithManager):
    def test_missing_parameters_fails(self):
        self.assertEqual(SetDailyReminderSkill(self.manager).execute({"at_time": "09:00"}).error, "MISSING_PARAMETERS")
        self.assertEqual(SetDailyReminderSkill(self.manager).execute({"text": "sveglia"}).error, "MISSING_PARAMETERS")

    def test_invalid_time_fails_gracefully(self):
        result = SetDailyReminderSkill(self.manager).execute({"text": "sveglia", "at_time": "non un orario"})
        self.assertEqual(result.error, "INVALID_TIME")

    def test_a_real_daily_reminder_is_saved_as_recurring(self):
        result = SetDailyReminderSkill(self.manager).execute({"text": "prendi le vitamine", "at_time": "08:00"})
        self.assertTrue(result.success)
        [reminder] = self.manager.list_upcoming()
        self.assertEqual(reminder["text"], "prendi le vitamine")
        self.assertEqual(reminder["recur_time"], "08:00")


class StartPomodoroCrashRegressionTests(_WithManager):
    """Il buco reale trovato e corretto in questa sessione."""

    def test_non_numeric_minutes_falls_back_to_the_default_instead_of_crashing(self):
        result = StartPomodoroSkill(self.manager).execute({"minutes": "trenta"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["minutes"], StartPomodoroSkill.DEFAULT_MINUTES)

    def test_zero_or_negative_minutes_falls_back_to_the_default(self):
        self.assertEqual(StartPomodoroSkill(self.manager).execute({"minutes": 0}).data["minutes"], StartPomodoroSkill.DEFAULT_MINUTES)
        self.assertEqual(StartPomodoroSkill(self.manager).execute({"minutes": -5}).data["minutes"], StartPomodoroSkill.DEFAULT_MINUTES)

    def test_missing_minutes_uses_the_default(self):
        result = StartPomodoroSkill(self.manager).execute({})
        self.assertEqual(result.data["minutes"], 25)


class StartStopPomodoroTests(_WithManager):
    def test_starting_a_pomodoro_creates_a_real_reminder(self):
        result = StartPomodoroSkill(self.manager).execute({"minutes": 20})
        self.assertTrue(result.success)
        [reminder] = self.manager.list_upcoming()
        self.assertIn("pomodoro", reminder["text"])

    def test_stopping_without_an_active_pomodoro_reports_not_found(self):
        result = StopPomodoroSkill(self.manager).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_stopping_removes_the_pomodoro_reminder(self):
        StartPomodoroSkill(self.manager).execute({"minutes": 20})
        result = StopPomodoroSkill(self.manager).execute({})
        self.assertTrue(result.success)
        self.assertEqual(self.manager.list_upcoming(), [])

    def test_stopping_does_not_remove_an_unrelated_reminder(self):
        self.manager.add("prendi la medicina", datetime.now(timezone.utc) + timedelta(minutes=5))
        result = StopPomodoroSkill(self.manager).execute({})
        self.assertEqual(result.error, "NOT_FOUND")
        self.assertEqual(len(self.manager.list_upcoming()), 1)


if __name__ == "__main__":
    unittest.main()
