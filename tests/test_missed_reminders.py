"""F6.1.5: dopo uno spegnimento i promemoria scaduti non partono tutti insieme uno per uno (a voce: una raffica), ma
in un solo riepilogo; quelli appena scaduti restano puntuali. ReminderManager reale su database temporaneo."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.reminder_manager import ReminderManager
from core.scheduler import ReminderScheduler


class MissedRemindersTests(unittest.TestCase):
    def test_reminders_missed_while_off_arrive_as_one_digest(self):
        from core.jake_core import JakeCore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        reminders = ReminderManager(Path(tmp.name) / "reminders.db")
        self.addCleanup(reminders.close)  # prima della cartella temporanea (ordine inverso)
        now = datetime.now(timezone.utc)
        for text, ago in (("chiamare Luca", timedelta(hours=3)), ("medicina", timedelta(hours=2)),
                          ("stendere", timedelta(seconds=20))):
            reminders.add(text, now - ago)

        core = JakeCore.__new__(JakeCore)
        core.present_notification = mock.MagicMock()
        scheduler = ReminderScheduler(reminders, interval_seconds=5)
        scheduler.on_due = mock.MagicMock()
        scheduler.on_missed = core._on_missed_reminders
        scheduler.tick()

        self.assertEqual([c.args[0]["text"] for c in scheduler.on_due.call_args_list], ["stendere"], "puntuale")
        kind, message = core.present_notification.call_args.args
        self.assertEqual(kind, "reminder")
        self.assertTrue(message.startswith("Mentre non ero attivo sono scaduti 2 promemoria: chiamare Luca (alle "), message)
        self.assertIn("medicina", message)
        scheduler.tick()
        self.assertEqual(core.present_notification.call_count, 1, "nessun doppione al giro dopo")

    def test_without_a_digest_handler_every_reminder_still_arrives(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        reminders = ReminderManager(Path(tmp.name) / "reminders.db")
        self.addCleanup(reminders.close)  # prima della cartella temporanea (ordine inverso)
        reminders.add("vecchio", datetime.now(timezone.utc) - timedelta(hours=5))
        scheduler = ReminderScheduler(reminders)
        scheduler.on_due = mock.MagicMock()
        scheduler.tick()
        self.assertEqual(scheduler.on_due.call_count, 1)


if __name__ == "__main__":
    unittest.main()
