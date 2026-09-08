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


class KnowledgeGraphTests(MemoryManagerTestCase):
    """v4.4, Personal Knowledge Graph: link()/related()/unlink()."""

    def test_related_returns_linked_memory_with_its_current_value(self):
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda di consegne")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")

        related = self.manager.related("Mario", "fact")

        self.assertEqual(len(related), 1)
        self.assertEqual(related[0], {"predicate": "lavora per", "key": "Acme", "category": "fact", "value": "un'azienda di consegne"})

    def test_related_with_no_links_is_empty(self):
        self.manager.remember("Mario", "un collega")
        self.assertEqual(self.manager.related("Mario", "fact"), [])

    def test_related_can_filter_by_predicate(self):
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda")
        self.manager.remember("Torino", "una citta'")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")
        self.manager.link("Mario", "fact", "vive a", "Torino", "fact")

        self.assertEqual([r["key"] for r in self.manager.related("Mario", "fact", predicate="vive a")], ["Torino"])

    def test_related_reflects_a_value_updated_after_linking(self):
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda piccola")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")
        self.manager.remember("Acme", "un'azienda grande ora")  # aggiorna lo stesso ricordo

        related = self.manager.related("Mario", "fact")
        self.assertEqual(related[0]["value"], "un'azienda grande ora")

    def test_forgetting_the_object_also_removes_the_incoming_edge(self):
        """forget() ripulisce ogni arco che tocca la chiave dimenticata, sia come soggetto sia
        come oggetto: un grafo senza archi che puntano a un nodo ormai cancellato."""
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")
        self.manager.forget("Acme")

        self.assertEqual(self.manager.related("Mario", "fact"), [])

    def test_related_shows_no_value_for_an_edge_pointing_to_a_key_that_no_longer_exists(self):
        """Difesa indipendente da forget(): related() non deve mai sollevare un errore se
        l'oggetto di un arco non esiste piu' per qualunque motivo, solo restituire value=None."""
        self.manager.remember("Mario", "un collega")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")  # 'Acme' mai salvato

        related = self.manager.related("Mario", "fact")
        self.assertEqual(related, [{"predicate": "lavora per", "key": "Acme", "category": "fact", "value": None}])

    def test_forgetting_the_subject_removes_its_outgoing_links(self):
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")
        self.manager.forget("Mario")

        self.assertEqual(self.manager.related("Mario", "fact"), [])

    def test_unlink_removes_the_relation(self):
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")

        removed = self.manager.unlink("Mario", "fact", "lavora per", "Acme", "fact")

        self.assertTrue(removed)
        self.assertEqual(self.manager.related("Mario", "fact"), [])

    def test_duplicate_link_is_a_no_op(self):
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")

        self.assertEqual(len(self.manager.related("Mario", "fact")), 1)


if __name__ == "__main__":
    unittest.main()
