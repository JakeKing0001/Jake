"""Test unitari per core/reminder_manager.py: nessuna suite esisteva finora, nonostante gestisca
sia i promemoria dell'utente sia i timer/pomodoro (stessa tabella, colonna 'kind') e sia
interrogata ogni 20 secondi da un thread separato (core/scheduler.py). Un database sqlite vero
su file temporaneo, non un finto: e' proprio l'interazione con SQL (confronto testuale di
timestamp ISO, ALTER TABLE idempotente) a essere la parte interessante da verificare.

F1: buco reale trovato e corretto in questa sessione. Un promemoria ricorrente (SET_DAILY_
REMINDER) rimasto scaduto per piu' di un giorno (es. Jake spento per qualche giorno) veniva
riprogrammato di +1 giorno alla volta: se il nuovo due_at restava comunque nel passato, la
chiamata SUCCESSIVA di due_reminders() (ogni 20s) lo faceva scattare di nuovo, e ancora, finche'
la data non raggiungeva oggi - un promemoria perso per 3 giorni suonava 4 volte di fila nel giro
di un minuto invece di una sola."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.reminder_manager import ReminderManager


class _WithManager(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_reminder_manager_test_"))
        self.manager = ReminderManager(db_path=tmp_dir / "reminders.db")

    def tearDown(self):
        self.manager.close()


def _utc(**kwargs) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**kwargs)


class AddAndDueReminderTests(_WithManager):
    def test_a_future_reminder_is_not_due_yet(self):
        self.manager.add("prendi la medicina", _utc(minutes=10))
        self.assertEqual(self.manager.due_reminders(), [])

    def test_a_past_reminder_is_due(self):
        self.manager.add("prendi la medicina", _utc(minutes=-1))
        due = self.manager.due_reminders()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["text"], "prendi la medicina")

    def test_a_fired_non_recurring_reminder_is_never_returned_again(self):
        self.manager.add("prendi la medicina", _utc(minutes=-1))
        self.manager.due_reminders()
        self.assertEqual(self.manager.due_reminders(), [])

    def test_multiple_due_reminders_are_returned_in_due_order(self):
        self.manager.add("secondo", _utc(minutes=-5))
        self.manager.add("primo", _utc(minutes=-10))
        due = self.manager.due_reminders()
        self.assertEqual([r["text"] for r in due], ["primo", "secondo"])


class RecurringReminderCatchUpTests(_WithManager):
    """Il buco reale trovato e corretto in questa sessione."""

    def test_a_recurring_reminder_missed_by_one_day_fires_once_and_reschedules_a_day_ahead(self):
        self.manager.add("promemoria giornaliero", _utc(hours=-25), recur_time="09:00")
        first = self.manager.due_reminders()
        self.assertEqual(len(first), 1)
        self.assertEqual(self.manager.due_reminders(), [])

    def test_a_recurring_reminder_missed_by_three_days_fires_exactly_once_not_three_times(self):
        self.manager.add("prendi la medicina", _utc(days=-3), recur_time="08:00")
        fired_counts = [len(self.manager.due_reminders()) for _ in range(5)]
        self.assertEqual(fired_counts, [1, 0, 0, 0, 0])

    def test_a_non_recurring_reminder_is_not_affected_by_the_catch_up_logic(self):
        self.manager.add("promemoria singolo", _utc(days=-3))
        fired_counts = [len(self.manager.due_reminders()) for _ in range(3)]
        self.assertEqual(fired_counts, [1, 0, 0])

    def test_the_rescheduled_due_date_is_strictly_in_the_future(self):
        self.manager.add("promemoria giornaliero", _utc(days=-3), recur_time="08:00")
        self.manager.due_reminders()
        [upcoming] = self.manager.list_upcoming(kind="reminder")
        rescheduled = datetime.fromisoformat(upcoming["due_at"])
        self.assertGreater(rescheduled, datetime.now(timezone.utc))


class ListUpcomingTests(_WithManager):
    def test_lists_only_the_requested_kind(self):
        self.manager.add("promemoria", _utc(minutes=5), kind="reminder")
        self.manager.add("timer pasta", _utc(minutes=5), kind="timer")
        reminders = self.manager.list_upcoming(kind="reminder")
        timers = self.manager.list_upcoming(kind="timer")
        self.assertEqual([r["text"] for r in reminders], ["promemoria"])
        self.assertEqual([r["text"] for r in timers], ["timer pasta"])

    def test_default_kind_is_reminder_not_everything(self):
        self.manager.add("promemoria", _utc(minutes=5), kind="reminder")
        self.manager.add("timer pasta", _utc(minutes=5), kind="timer")
        self.assertEqual([r["text"] for r in self.manager.list_upcoming()], ["promemoria"])

    def test_fired_reminders_are_excluded(self):
        self.manager.add("scaduto", _utc(minutes=-1))
        self.manager.due_reminders()
        self.assertEqual(self.manager.list_upcoming(), [])

    def test_limit_is_respected(self):
        for i in range(5):
            self.manager.add(f"promemoria {i}", _utc(minutes=i + 1))
        self.assertEqual(len(self.manager.list_upcoming(limit=2)), 2)


class FindDeleteSnoozeTests(_WithManager):
    def test_find_matching_is_a_case_sensitive_substring_search_within_the_kind(self):
        self.manager.add("prendi la medicina", _utc(minutes=5))
        self.assertIsNotNone(self.manager.find_matching("medicina"))
        self.assertIsNone(self.manager.find_matching("medicina", kind="timer"))

    def test_find_matching_returns_none_when_nothing_matches(self):
        self.assertIsNone(self.manager.find_matching("qualsiasi cosa"))

    def test_delete_matching_removes_the_reminder_and_returns_it(self):
        self.manager.add("prendi la medicina", _utc(minutes=5))
        removed = self.manager.delete_matching("medicina")
        self.assertEqual(removed["text"], "prendi la medicina")
        self.assertIsNone(self.manager.find_matching("medicina"))

    def test_delete_matching_returns_none_when_nothing_matches(self):
        self.assertIsNone(self.manager.delete_matching("non esiste"))

    def test_snooze_matching_pushes_the_due_date_forward(self):
        self.manager.add("prendi la medicina", _utc(minutes=-1))
        snoozed = self.manager.snooze_matching("medicina", minutes=15)
        self.assertIsNotNone(snoozed)
        new_due = datetime.fromisoformat(snoozed["due_at"])
        self.assertGreater(new_due, datetime.now(timezone.utc) + timedelta(minutes=10))

    def test_snooze_matching_returns_none_when_nothing_matches(self):
        self.assertIsNone(self.manager.snooze_matching("non esiste", minutes=5))


class SchemaResilienceTests(unittest.TestCase):
    def test_reopening_the_same_database_does_not_fail_on_repeated_alter_table(self):
        """_init_schema tenta ALTER TABLE a ogni apertura per aggiungere colonne introdotte dopo
        la prima versione dello schema: non deve fallire quando le colonne esistono gia'."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_reminder_manager_schema_test_"))
        db_path = tmp_dir / "reminders.db"
        first = ReminderManager(db_path=db_path)
        first.add("qualcosa", _utc(minutes=5))
        first.close()

        second = ReminderManager(db_path=db_path)  # non deve sollevare
        self.assertEqual(len(second.list_upcoming()), 1)
        second.close()


if __name__ == "__main__":
    unittest.main()
