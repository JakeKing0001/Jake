"""Test unitari per le nuove funzionalità di TodoManager relative alla gestione di goal e passi (F6.4.3/F6.4.4)."""
import tempfile
import unittest
import shutil
from pathlib import Path

from core.todo_manager import TodoManager


class TodoManagerGoalStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_todo_goal_step_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.manager = TodoManager(db_path=self.tmp_dir / "todo.db")
        self.addCleanup(self.manager.close)

    def test_completing_goal_completes_child_steps(self):
        """Quando si completa un goal, tutti i suoi passi figli devono essere completati."""
        # Aggiungi un goal con due passi
        goal_id = self.manager.add("finire la tesi")
        step1_id = self.manager.add("scrivere introduzione", parent_id=goal_id)
        step2_id = self.manager.add("fare bibliografia", parent_id=goal_id)

        # Verifica che tutti siano inizialmente aperti
        pending = self.manager.list_pending()
        self.assertEqual(len(pending), 1)  # Solo il goal viene mostrato in list_pending
        goal_item = pending[0]
        self.assertEqual(goal_item["text"], "finire la tesi")
        self.assertEqual(goal_item["steps_total"], 2)
        self.assertEqual(goal_item["steps_done"], 0)
        self.assertEqual(goal_item["next_step"], "scrivere introduzione")

        # Completa il goal
        result = self.manager.complete_matching("finire la tesi")
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "finire la tesi")

        # Verifica che goal e passi siano tutti completati
        pending = self.manager.list_pending()
        self.assertEqual(len(pending), 0)  # Niente più pending

        # Verifica direttamente nel database che tutti siano marcati come fatti
        cursor = self.manager._connection.cursor()
        cursor.execute("SELECT id, text, done FROM todos ORDER BY id")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 3)  # goal + 2 passi
        self.assertEqual({row[0] for row in rows}, {goal_id, step1_id, step2_id})
        for row in rows:
            self.assertEqual(row[2], 1)  # done = 1 per tutti

    def test_deleting_goal_deletes_child_steps(self):
        """Quando si elimina un goal, tutti i suoi passi figli devono essere eliminati."""
        # Aggiungi un goal con due passi
        goal_id = self.manager.add("finire la tesi")
        step1_id = self.manager.add("scrivere introduzione", parent_id=goal_id)
        step2_id = self.manager.add("fare bibliografia", parent_id=goal_id)

        # Verifica che esistano
        pending = self.manager.list_pending()
        self.assertEqual(len(pending), 1)
        rows = self.manager._connection.execute("SELECT id FROM todos").fetchall()
        self.assertEqual({row[0] for row in rows}, {goal_id, step1_id, step2_id})

        # Elimina il goal
        result = self.manager.delete_matching("finire la tesi")
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "finire la tesi")

        # Verifica che goal e passi siano tutti eliminati
        pending = self.manager.list_pending()
        self.assertEqual(len(pending), 0)  # Niente più pending

        # Verifica direttamente nel database che non ci siano più righe
        cursor = self.manager._connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM todos")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 0)  # Nessuna riga rimanente

    def test_completing_step_does_not_affect_goal(self):
        """Quando si completa un passo singolo, il goal rimane aperto."""
        # Aggiungi un goal con due passi
        goal_id = self.manager.add("finire la tesi")
        step1_id = self.manager.add("scrivere introduzione", parent_id=goal_id)
        step2_id = self.manager.add("fare bibliografia", parent_id=goal_id)

        # Verifica stato iniziale
        pending = self.manager.list_pending()
        self.assertEqual(len(pending), 1)
        goal_item = pending[0]
        self.assertEqual(goal_item["steps_total"], 2)
        self.assertEqual(goal_item["steps_done"], 0)
        self.assertEqual(goal_item["next_step"], "scrivere introduzione")

        # Completa solo il primo passo
        result = self.manager.complete_matching("scrivere introduzione")
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "scrivere introduzione")

        # Verifica che il goal rimanga aperto con un passo fatto
        pending = self.manager.list_pending()
        self.assertEqual(len(pending), 1)  # Il goal è ancora pending
        goal_item = pending[0]
        self.assertEqual(goal_item["text"], "finire la tesi")
        self.assertEqual(goal_item["steps_total"], 2)
        self.assertEqual(goal_item["steps_done"], 1)  # Un passo fatto
        self.assertEqual(goal_item["next_step"], "fare bibliografia")  # Il prossimo passo

        # Verifica nel database
        cursor = self.manager._connection.cursor()
        cursor.execute("SELECT id, text, done FROM todos WHERE id = ?", (goal_id,))
        goal_row = cursor.fetchone()
        self.assertEqual(goal_row[2], 0)  # goal non fatto

        cursor.execute("SELECT id, text, done FROM todos WHERE id IN (?, ?)", (step1_id, step2_id))
        step_rows = cursor.fetchall()
        self.assertEqual(len(step_rows), 2)
        # Un passo fatto, uno no
        done_count = sum(1 for row in step_rows if row[2] == 1)
        self.assertEqual(done_count, 1)

    def test_list_stale_pending_only_returns_top_level_items(self):
        """list_stale_pending deve restituire solo gli elementi di primo livello (goal), non i passi figli."""
        # Aggiungi un goal vecchio con un passo vecchio
        goal_id = self.manager.add("obiettivo vecchio")
        step_id = self.manager.add("passo vecchio", parent_id=goal_id)

        # Aggiungi un todo semplice vecchio
        todo_id = self.manager.add("todo semplice vecchio")

        # Fai backdate a tutti per farli sembrare vecchi
        old_date = "2000-01-01T00:00:00+00:00"
        self.manager._connection.execute("UPDATE todos SET created_at = ? WHERE id IN (?, ?, ?)",
                                       (old_date, goal_id, step_id, todo_id))
        self.manager._connection.commit()

        # Verifica che tutti siano nel database
        cursor = self.manager._connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM todos WHERE done = 0")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 3)  # goal + passo + todo

        # list_stale_pending dovrebbe restituire solo gli elementi di primo livello
        stale = self.manager.list_stale_pending(days=1)
        self.assertEqual(len(stale), 2)  # Solo goal e todo semplice, non il passo

        stale_texts = [item["text"] for item in stale]
        self.assertIn("obiettivo vecchio", stale_texts)
        self.assertIn("todo semplice vecchio", stale_texts)
        self.assertNotIn("passo vecchio", stale_texts)

    def test_goal_with_completed_steps_appears_in_stale_if_goal_not_done(self):
        """Un goal con tutti i passi completati ma il goal stesso NON completato
        deve apparire in list_stale_pending se vecchio, perché è comunque un top-level item pending."""
        # Aggiungi un goal vecchio con passi vecchi
        goal_id = self.manager.add("obiettivo vecchio con passi fatti")
        step1_id = self.manager.add("passo 1 vecchio", parent_id=goal_id)
        step2_id = self.manager.add("passo 2 vecchio", parent_id=goal_id)

        # Completa i passi (ma NON il goal stesso)
        self.manager.complete_matching("passo 1 vecchio")
        self.manager.complete_matching("passo 2 vecchio")

        # Fai backdate a tutti per farli sembrare vecchi
        old_date = "2000-01-01T00:00:00+00:00"
        self.manager._connection.execute("UPDATE todos SET created_at = ? WHERE id IN (?, ?, ?)",
                                       (old_date, goal_id, step1_id, step2_id))
        self.manager._connection.commit()

        # Verifica che i passi siano completati ma il goal no
        pending = self.manager.list_pending()
        goal_items = [item for item in pending if item["text"] == "obiettivo vecchio con passi fatti"]
        self.assertEqual(len(goal_items), 1)
        goal_item = goal_items[0]
        self.assertEqual(goal_item["steps_total"], 2)
        self.assertEqual(goal_item["steps_done"], 2)  # Tutti i passi fatti
        self.assertIsNone(goal_item["next_step"])  # Nessun prossimo passo
        # Importante: il goal stesso NON è segnato come fatto

        # Questo goal, nonostante abbia i passi completati, deve apparire in list_stale_pending
        # perché è un top-level item (parent_id IS NULL) che è vecchio e non completato (done = 0)
        stale = self.manager.list_stale_pending(days=1)
        stale_texts = [item["text"] for item in stale]
        self.assertIn("obiettivo vecchio con passi fatti", stale_texts)

if __name__ == "__main__":
    unittest.main()
