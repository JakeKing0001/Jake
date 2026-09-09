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


class ProvenanceAndExpiryTests(MemoryManagerTestCase):
    """F5 (Memory 2.0, provenienza e scadenza - vedi ROADMAP.md): source dice chi ha detto un
    fatto (utente/inferenza/agente), expires_at (da ttl_days) quando smette di valere da solo."""

    def test_default_source_is_user(self):
        self.manager.remember("colore preferito", "blu")

        self.assertEqual(self.manager.recall(key="colore preferito")[0]["source"], "user")

    def test_explicit_source_is_stored(self):
        self.manager.remember("umore probabile", "stanco", source="inferred")

        self.assertEqual(self.manager.recall(key="umore probabile")[0]["source"], "inferred")

    def test_memory_without_ttl_never_expires(self):
        self.manager.remember("compleanno", "5 marzo")

        self.assertIsNone(self.manager.recall(key="compleanno")[0]["expires_at"])

    def test_expired_memory_is_hidden_from_recall_by_default(self):
        self.manager.remember("meteo oggi", "piove", ttl_days=1)
        self.manager._connection.execute(
            "UPDATE memories SET expires_at = ? WHERE key = ?", ("2020-01-01T00:00:00+00:00", "meteo oggi"),
        )
        self.manager._connection.commit()

        self.assertEqual(self.manager.recall(key="meteo oggi"), [])

    def test_expired_memory_is_visible_with_include_expired(self):
        self.manager.remember("meteo oggi", "piove", ttl_days=1)
        self.manager._connection.execute(
            "UPDATE memories SET expires_at = ? WHERE key = ?", ("2020-01-01T00:00:00+00:00", "meteo oggi"),
        )
        self.manager._connection.commit()

        results = self.manager.recall(key="meteo oggi", include_expired=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["value"], "piove")

    def test_not_yet_expired_memory_stays_visible(self):
        self.manager.remember("meteo oggi", "piove", ttl_days=1)

        self.assertEqual(len(self.manager.recall(key="meteo oggi")), 1)

    def test_purge_expired_removes_only_expired_memories(self):
        self.manager.remember("meteo oggi", "piove", ttl_days=1)
        self.manager.remember("compleanno", "5 marzo")
        self.manager._connection.execute(
            "UPDATE memories SET expires_at = ? WHERE key = ?", ("2020-01-01T00:00:00+00:00", "meteo oggi"),
        )
        self.manager._connection.commit()

        removed = self.manager.purge_expired()

        self.assertEqual(removed, 1)
        self.assertEqual(self.manager.recall(key="meteo oggi", include_expired=True), [])
        self.assertEqual(len(self.manager.recall(key="compleanno")), 1)

    def test_purge_expired_with_nothing_to_remove_returns_zero(self):
        self.manager.remember("compleanno", "5 marzo")

        self.assertEqual(self.manager.purge_expired(), 0)

    def test_semantic_recall_also_hides_expired_memories_by_default(self):
        self.manager.remember("nota", "scade oggi", embedding=SAME_MEANING, ttl_days=1)
        self.manager._connection.execute(
            "UPDATE memories SET expires_at = ? WHERE key = ?", ("2020-01-01T00:00:00+00:00", "nota"),
        )
        self.manager._connection.commit()

        self.assertEqual(self.manager.semantic_recall(SAME_MEANING), [])
        self.assertEqual(len(self.manager.semantic_recall(SAME_MEANING, include_expired=True)), 1)


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


class PurgeHistoryOlderThanTests(MemoryManagerTestCase):
    """v5.6, Privacy Engine: retention su richiesta esplicita."""

    def _log_turn_at(self, role, text, iso_timestamp):
        self.manager.log_turn(role, text)
        self.manager._connection.execute(
            "UPDATE conversation_history SET created_at = ? WHERE id = (SELECT MAX(id) FROM conversation_history)",
            (iso_timestamp,),
        )
        self.manager._connection.commit()

    def test_old_turns_are_removed_recent_ones_kept(self):
        self._log_turn_at("user", "vecchio messaggio", "2000-01-01T00:00:00+00:00")
        self._log_turn_at("user", "messaggio recente", "2999-01-01T00:00:00+00:00")

        removed = self.manager.purge_history_older_than(days=30)

        self.assertEqual(removed, 1)
        remaining = [row["text"] for row in self.manager.get_recent_history(limit=10)]
        self.assertEqual(remaining, ["messaggio recente"])

    def test_old_summaries_are_removed_too(self):
        self.manager.remember("riassunto vecchio", "contenuto", category="summary")
        self.manager._connection.execute(
            "UPDATE memories SET created_at = ? WHERE key = ?", ("2000-01-01T00:00:00+00:00", "riassunto vecchio"),
        )
        self.manager._connection.commit()

        removed = self.manager.purge_history_older_than(days=30)

        self.assertEqual(removed, 1)
        self.assertEqual(self.manager.count_memories(), 0)

    def test_does_not_touch_facts_or_preferences(self):
        """Solo cronologia/riassunti: un fatto salvato esplicitamente con REMEMBER non e' mai
        cancellato da una politica di retention automatica, anche se molto vecchio."""
        self.manager.remember("compleanno", "5 marzo", category="fact")
        self.manager._connection.execute(
            "UPDATE memories SET created_at = ? WHERE key = ?", ("2000-01-01T00:00:00+00:00", "compleanno"),
        )
        self.manager._connection.commit()

        removed = self.manager.purge_history_older_than(days=30)

        self.assertEqual(removed, 0)
        self.assertEqual(self.manager.count_memories(), 1)

    def test_nothing_to_remove_returns_zero(self):
        self.assertEqual(self.manager.purge_history_older_than(days=30), 0)


if __name__ == "__main__":
    unittest.main()
