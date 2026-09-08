"""Test unitari per il dedup semantico e le query temporali di MemoryManager (v3.4, fase Memory
2.0). Usa un file sqlite temporaneo per test (mai il database vero di produzione) ed embedding
finti (semplici vettori 2D): cosine_similarity e' pura matematica, non richiede Ollama."""
import shutil
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager

SAME_MEANING = [1.0, 0.02]       # quasi parallelo a SAME_MEANING_2 -> similarita' alta
SAME_MEANING_2 = [1.0, 0.05]
UNRELATED = [0.0, 1.0]           # ortogonale -> similarita' ~0


class MemoryManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_memory_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.manager = MemoryManager(db_path=self.tmp_dir / "memory.db")
        self.addCleanup(self.manager.close)


class SemanticDedupTests(MemoryManagerTestCase):
    def test_near_identical_restatement_updates_the_same_memory(self):
        self.manager.remember("compleanno", "5 marzo", embedding=SAME_MEANING)
        self.manager.remember("data di nascita", "5 di marzo", embedding=SAME_MEANING_2)

        self.assertEqual(self.manager.count_memories(), 1)
        results = self.manager.recall(query="marzo")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["key"], "compleanno")
        self.assertEqual(results[0]["value"], "5 di marzo")

    def test_unrelated_memory_is_not_merged(self):
        self.manager.remember("compleanno", "5 marzo", embedding=SAME_MEANING)
        self.manager.remember("colore preferito", "blu", embedding=UNRELATED)

        self.assertEqual(self.manager.count_memories(), 2)

    def test_dedup_respects_category_boundary(self):
        self.manager.remember("gusto_caffe", "mi piace il caffe", category="preference", embedding=SAME_MEANING)
        self.manager.remember("nota_caffe", "abbiamo parlato di caffe ieri", category="fact", embedding=SAME_MEANING_2)

        self.assertEqual(self.manager.count_memories(), 2, "categorie diverse non vanno mai fuse, anche se semanticamente simili")

    def test_exact_key_update_does_not_need_dedup_redirection(self):
        self.manager.remember("compleanno", "5 marzo", embedding=SAME_MEANING)
        self.manager.remember("compleanno", "6 marzo", embedding=SAME_MEANING)

        self.assertEqual(self.manager.count_memories(), 1)
        self.assertEqual(self.manager.recall(key="compleanno")[0]["value"], "6 marzo")

    def test_memories_without_embedding_are_never_deduped(self):
        self.manager.remember("compleanno", "5 marzo")
        self.manager.remember("data di nascita", "5 di marzo")

        self.assertEqual(self.manager.count_memories(), 2)


class TemporalQueryTests(MemoryManagerTestCase):
    def test_since_and_until_filter_by_updated_at(self):
        self.manager.remember("a", "vecchio")
        self.manager._connection.execute("UPDATE memories SET updated_at = ? WHERE key = ?", ("2020-01-01T00:00:00+00:00", "a"))
        self.manager.remember("b", "recente")
        self.manager._connection.execute("UPDATE memories SET updated_at = ? WHERE key = ?", ("2030-01-01T00:00:00+00:00", "b"))
        self.manager._connection.commit()

        self.assertEqual([r["key"] for r in self.manager.recall(since="2025-01-01T00:00:00+00:00")], ["b"])
        self.assertEqual([r["key"] for r in self.manager.recall(until="2025-01-01T00:00:00+00:00")], ["a"])
        self.assertEqual(
            {r["key"] for r in self.manager.recall(since="2019-01-01T00:00:00+00:00", until="2031-01-01T00:00:00+00:00")},
            {"a", "b"},
        )


if __name__ == "__main__":
    unittest.main()
