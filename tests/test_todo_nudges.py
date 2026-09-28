"""F6.4.5 ("rinegoziare invece di inviare reminder infiniti"): prova reale del 28/09/2026 - "comprare il pane" e
"la cartella Download ha accumulato 4.2 GB" venivano riannunciati a ogni avvio di Jake (tre volte in sei minuti),
perche' il "gia' detto" stava solo in memoria. Database delle todo vero, file di stato vero, advisor ricreato per
simulare il riavvio."""
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.system_advisor import TODO_MAX_NUDGES, SystemAdvisor
from core.todo_manager import TodoManager


class TodoNudgeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.todos = TodoManager(self.dir / "todos.db")
        self.addCleanup(self.todos.close)
        self.todo_id = self.todos.add("comprare il pane")
        self._set(created_at=(datetime.now(timezone.utc) - timedelta(days=9)).isoformat())

    def _set(self, **columns):
        connection = sqlite3.connect(self.dir / "todos.db")
        for column, value in columns.items():
            connection.execute(f"UPDATE todos SET {column} = ? WHERE id = ?", (value, self.todo_id))
        connection.commit()
        connection.close()

    def _start_jake(self) -> list:
        """Un avvio di Jake: advisor nuovo, stessi dati su disco."""
        said: list = []
        advisor = SystemAdvisor(on_advisory=said.append, todo_manager=self.todos, downloads_dir=self.dir / "nessuna",
                                state_path=self.dir / "advisor_state.json")
        advisor._check_stale_todos()
        return said

    def test_a_restart_does_not_repeat_a_todo_just_mentioned(self):
        self.assertEqual(self._start_jake(), ["C'e' un'attivita' in sospeso da un po' nella todo list: \"comprare il pane\"."])
        self.assertEqual(self._start_jake(), [], "riavvio subito dopo: gia' detto")
        self.assertEqual(self._start_jake(), [])

    def test_the_second_time_jake_offers_to_close_it_and_after_three_it_stops(self):
        self._start_jake()
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3, minutes=1)).isoformat()
        self._set(nudged_at=three_days_ago)

        said = self._start_jake()

        self.assertEqual(len(said), 1)
        self.assertIn("è in lista da 9 giorni e te l'ho già ricordata", said[0])
        self.assertIn('"segna come fatto comprare il pane"', said[0])
        self.assertIn('"togli dalla lista comprare il pane"', said[0])
        self._set(nudges=TODO_MAX_NUDGES, nudged_at=three_days_ago)
        self.assertEqual(self._start_jake(), [], "dopo tre promemoria Jake smette: resta nella lista")


class DownloadsWarningTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.downloads = self.dir / "Download"
        self.downloads.mkdir()
        old = time.time() - 40 * 86400
        for index in range(25):   # 25 file vecchi: sopra la soglia dei file dimenticati
            path = self.downloads / f"vecchio_{index}.txt"
            path.write_text("x")
            os.utime(path, (old, old))

    def _start_jake(self) -> list:
        said: list = []
        SystemAdvisor(on_advisory=said.append, downloads_dir=self.downloads,
                      state_path=self.dir / "advisor_state.json")._check_downloads_clutter()
        return said

    def test_the_downloads_warning_survives_a_restart(self):
        self.assertEqual(len(self._start_jake()), 1)
        self.assertEqual(self._start_jake(), [], "riavvio: gia' detto questa settimana")

    def test_after_a_week_it_can_be_said_again(self):
        self._start_jake()
        state = self.dir / "advisor_state.json"
        state.write_text('{"downloads_warned_at": %f}' % (time.time() - 8 * 86400), encoding="utf-8")
        self.assertEqual(len(self._start_jake()), 1)


if __name__ == "__main__":
    unittest.main()
