"""Test unitari per TodoManager.list_stale_pending (v4.2, Proactive Intelligence). Usa un file
sqlite temporaneo, mai il database vero di produzione."""
import shutil
import tempfile
import unittest
from pathlib import Path

from core.todo_manager import TodoManager


class ListStalePendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_todo_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.manager = TodoManager(db_path=self.tmp_dir / "todo.db")
        self.addCleanup(self.manager.close)

    def _backdate(self, todo_id: int, iso_timestamp: str) -> None:
        self.manager._connection.execute("UPDATE todos SET created_at = ? WHERE id = ?", (iso_timestamp, todo_id))
        self.manager._connection.commit()

    def test_recently_added_todo_is_not_stale(self):
        self.manager.add("comprare il latte")
        self.assertEqual(self.manager.list_stale_pending(days=3), [])

    def test_old_todo_is_stale(self):
        todo_id = self.manager.add("pagare la bolletta")
        self._backdate(todo_id, "2000-01-01T00:00:00+00:00")
        stale = self.manager.list_stale_pending(days=3)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["text"], "pagare la bolletta")

    def test_completed_old_todo_is_not_returned(self):
        todo_id = self.manager.add("gia' fatta")
        self._backdate(todo_id, "2000-01-01T00:00:00+00:00")
        self.manager.complete_matching("gia' fatta")
        self.assertEqual(self.manager.list_stale_pending(days=3), [])

    def test_ordered_oldest_first(self):
        newer_id = self.manager.add("piu' recente")
        older_id = self.manager.add("piu' vecchia")
        self._backdate(newer_id, "2000-02-01T00:00:00+00:00")
        self._backdate(older_id, "2000-01-01T00:00:00+00:00")
        stale = self.manager.list_stale_pending(days=3)
        self.assertEqual([todo["text"] for todo in stale], ["piu' vecchia", "piu' recente"])


if __name__ == "__main__":
    unittest.main()
