"""F5.7 nel percorso reale degli skill (database temporaneo, dati sintetici).

Prima: "dimentica X" (FORGET) cancellava ricordo e relazioni ma lasciava la chiave nel registro eventi
(memory_audit) e non produceva prova; l'uso dei ricordi (F5.7.3) non veniva mai registrato; la
spiegazione della provenienza esisteva solo nella libreria."""
import json
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from core.skill_catalog import build_memory_notes_todo_skills


class MemoryPrivacySkillTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)
        self.skills = build_memory_notes_todo_skills(self.memory, None, None)
        self.receipts = Path(tmp.name) / "memory_deletion_receipts.jsonl"
        self.memory.remember("indirizzo di prova", "via Sintetica 12", source="user")

    def _audit_rows(self, key):
        with self.memory.lock:
            return self.memory.connection.execute(
                "SELECT COUNT(*) FROM memory_audit WHERE memory_key = ?", (key,)).fetchone()[0]

    def test_recall_records_the_use_except_in_private_mode(self):
        recall = self.skills["RECALL"]
        self.assertTrue(recall.execute({"key": "indirizzo di prova"}).success)
        record = self.skills["EXPLAIN_MEMORY"].dashboard.get("indirizzo di prova")
        self.assertEqual(record.use_count, 1)
        recall.private_mode_provider = lambda: True
        recall.execute({"key": "indirizzo di prova"})
        self.assertEqual(self.skills["EXPLAIN_MEMORY"].dashboard.get("indirizzo di prova").use_count, 1)

    def test_explain_memory_says_where_a_memory_comes_from(self):
        self.skills["RECALL"].execute({"key": "indirizzo di prova"})
        result = self.skills["EXPLAIN_MEMORY"].execute({"key": "indirizzo"})
        self.assertTrue(result.success)
        text = result.data["explanations"][0]
        self.assertIn("detto esplicitamente dall'utente", text)
        self.assertIn("Usato 1 volte", text)
        self.assertFalse(self.skills["EXPLAIN_MEMORY"].execute({"key": "inesistente"}).success)

    def test_forget_removes_the_audit_trail_and_leaves_a_hash_only_receipt(self):
        self.skills["RECALL"].execute({"key": "indirizzo di prova"})
        self.assertGreater(self._audit_rows("indirizzo di prova"), 0)

        result = self.skills["FORGET"].execute({"key": "indirizzo di prova"})

        self.assertTrue(result.success)
        self.assertTrue(result.data["verified"])
        self.assertEqual(self._audit_rows("indirizzo di prova"), 0, "la chiave non resta nel registro eventi")
        self.assertEqual(self.memory.recall(key="indirizzo di prova"), [])
        receipt = self.receipts.read_text(encoding="utf-8")
        self.assertNotIn("indirizzo", receipt)
        self.assertNotIn("Sintetica", receipt)
        self.assertEqual(json.loads(receipt.splitlines()[0])["memory_hash"], result.data["receipts"][0])
        self.assertEqual(self.skills["FORGET"].execute({"key": "indirizzo di prova"}).error, "NOT_FOUND")

    def test_forget_also_removes_the_copy_in_the_conversation_history(self):
        """Senza questo il valore restava nella cronologia e la cancellazione non era verificabile."""
        self.memory.log_turn("user", "ricordati che il mio indirizzo e' VIA SINTETICA 12, grazie")
        self.memory.log_turn("jake", "Ok, lo ricordero'.")

        result = self.skills["FORGET"].execute({"key": "indirizzo di prova"})

        self.assertTrue(result.data["verified"])
        self.assertEqual(result.data["residue_check"], "clean")
        with self.memory.lock:
            texts = [row[0] for row in self.memory.connection.execute("SELECT text FROM conversation_history")]
        self.assertEqual(texts, ["ricordati che il mio indirizzo e' [dimenticato], grazie", "Ok, lo ricordero'."])


if __name__ == "__main__":
    unittest.main()
