"""Test unitari per TodoManager.list_stale_pending (v4.2, Proactive Intelligence). Usa un file
sqlite temporaneo, mai il database vero di produzione."""
import shutil
import tempfile
import threading
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


class ConcurrentAccessTests(unittest.TestCase):
    """F1.8.2 ("serializzare azioni che toccano lo stesso resource key"): TodoManager e'
    `check_same_thread=False` perche' SystemAdvisor legge list_stale_pending() da un thread
    separato mentre il thread principale puo' scrivere nello stesso istante. Verificato che
    add() da piu' thread contemporaneamente non perda ne' duplichi righe, e che complete_
    matching() (SELECT poi UPDATE sullo stesso id) non faccia mai completare due volte lo
    stesso identico todo quando piu' thread lo cercano nello stesso istante."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_todo_concurrency_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.manager = TodoManager(db_path=self.tmp_dir / "todo.db")
        self.addCleanup(self.manager.close)

    def test_concurrent_add_from_many_threads_loses_nothing(self):
        thread_count, per_thread = 15, 15
        barrier = threading.Barrier(thread_count)

        def _add_many(thread_index: int):
            barrier.wait()
            for i in range(per_thread):
                self.manager.add(f"task-{thread_index}-{i}")

        threads = [threading.Thread(target=_add_many, args=(i,)) for i in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        pending = self.manager.list_pending(limit=thread_count * per_thread + 10)
        self.assertEqual(len(pending), thread_count * per_thread)
        self.assertEqual(len({todo["text"] for todo in pending}), thread_count * per_thread)

    def test_concurrent_complete_matching_on_the_same_task_completes_it_exactly_once(self):
        """Buco reale che il lock evita: complete_matching() fa prima una SELECT poi una UPDATE
        sull'id trovato - senza serializzare l'intero metodo, due thread potrebbero trovare
        entrambi la riga 'done = 0' prima che uno dei due la aggiorni, e uno dei due
        restituirebbe un task "completato" che in realta' l'altro thread ha gia' gestito."""
        self.manager.add("task unico da completare una volta sola")
        thread_count = 10
        barrier = threading.Barrier(thread_count)
        results: list[dict | None] = [None] * thread_count

        def _try_complete(index: int):
            barrier.wait()
            results[index] = self.manager.complete_matching("task unico")

        threads = [threading.Thread(target=_try_complete, args=(i,)) for i in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        successes = [r for r in results if r is not None]
        self.assertEqual(len(successes), 1, "esattamente un thread deve trovare/completare il task")
        self.assertEqual(self.manager.list_pending(), [])


if __name__ == "__main__":
    unittest.main()
