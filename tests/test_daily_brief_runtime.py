"""F6.6 nel runtime: core/daily_brief.py esisteva ma nessun comando lo usava. "Com'e' la mia giornata" ora legge i
promemoria e le todo veri (database temporanei, dati sintetici) e ogni riga dichiara la sua fonte."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from core.reminder_manager import ReminderManager
from core.response_formatter import format_skill_result
from core.todo_manager import TodoManager
from skills.daily_brief import DailyBriefSkill


class DailyBriefRuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.reminders = ReminderManager(Path(tmp.name) / "reminders.db")
        self.todos = TodoManager(Path(tmp.name) / "todos.db")
        for manager in (self.reminders, self.todos):
            self.addCleanup(getattr(manager, "close", lambda: None))

    def test_the_brief_lists_todays_reminders_and_open_todos_with_their_source(self):
        now = datetime.now()
        self.reminders.add("chiamare il dentista", now + timedelta(hours=2))
        self.reminders.add("rinnovo passaporto", now + timedelta(days=5))  # oltre le 24 ore: non oggi
        self.todos.add("comprare il latte")

        result = DailyBriefSkill(self.reminders, self.todos).execute({})
        self.assertTrue(result.success)
        reply = format_skill_result("DAILY_BRIEF", result)
        self.assertIn("chiamare il dentista", reply)
        self.assertIn("comprare il latte", reply)
        self.assertNotIn("passaporto", reply)
        self.assertEqual({p["source"] for p in result.data["provenance"]}, {"promemoria", "todo"})
        self.assertEqual(result.data["unavailable"], [])

    def test_a_broken_source_is_declared_not_invented_and_details_show_age(self):
        self.todos.add("pagare la bolletta")
        with mock.patch.object(self.reminders, "list_upcoming", side_effect=RuntimeError("database bloccato")):
            result = DailyBriefSkill(self.reminders, self.todos).execute({"detailed": True})
        reply = format_skill_result("DAILY_BRIEF", result)
        self.assertEqual(result.data["unavailable"], ["promemoria"])
        self.assertIn("La fonte 'promemoria' non ha risposto", reply)
        self.assertIn("pagare la bolletta [todo, adesso]", reply)

    def test_an_empty_day_says_so(self):
        reply = format_skill_result("DAILY_BRIEF", DailyBriefSkill(self.reminders, self.todos).execute({}))
        # le fonti hanno risposto: una giornata vuota non e' "nessun dato" (e non e' sempre mattina)
        self.assertEqual(reply, "Niente in programma: promemoria e todo non riportano nulla.")


if __name__ == "__main__":
    unittest.main()
