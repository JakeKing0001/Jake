"""Test unitari per il dedup semantico e le query temporali di MemoryManager (v3.4, fase Memory
2.0). Usa un file sqlite temporaneo per test (mai il database vero di produzione) ed embedding
finti (semplici vettori 2D): cosine_similarity e' pura matematica, non richiede Ollama."""
import shutil
import sqlite3
import tempfile
import threading
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


class ConcurrentAccessTests(MemoryManagerTestCase):
    """F1.8.2 ("serializzare azioni che toccano lo stesso resource key"): stesso principio gia'
    applicato a core/reminder_manager.py/core/todo_manager.py - la connessione e'
    check_same_thread=False perche' TriggerScheduler legge/scrive WorkflowManager/TriggerManager
    (entrambi backed da questa connessione) da un thread separato, ma senza un lock proprio
    l'accesso concorrente reale puo' sollevare sqlite3.InterfaceError o corrompere i dati (vedi
    la stessa dimostrazione empirica gia' fatta per TodoManager)."""

    def test_concurrent_remember_from_many_threads_loses_nothing(self):
        thread_count, per_thread = 15, 15
        barrier = threading.Barrier(thread_count)

        def _remember_many(thread_index: int):
            barrier.wait()
            for i in range(per_thread):
                self.manager.remember(f"chiave-{thread_index}-{i}", f"valore-{thread_index}-{i}")

        threads = [threading.Thread(target=_remember_many, args=(i,)) for i in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(self.manager.count_memories(), thread_count * per_thread)

    def test_concurrent_log_turn_does_not_corrupt_history(self):
        """log_turn() e' un INSERT seguito da una DELETE di pulizia (due istruzioni, non una
        sola): il caso piu' simile a due_reminders() (core/reminder_manager.py), gia' dimostrato
        vulnerabile senza un lock che copra l'intero metodo."""
        thread_count, per_thread = 10, 10
        barrier = threading.Barrier(thread_count)

        def _log_many(thread_index: int):
            barrier.wait()
            for i in range(per_thread):
                self.manager.log_turn("user", f"turno-{thread_index}-{i}")

        threads = [threading.Thread(target=_log_many, args=(i,)) for i in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # MAX_HISTORY_ENTRIES tronca la cronologia: qui il totale (100) resta sotto la soglia
        # (200), quindi nessuna riga deve mancare.
        history = self.manager.get_recent_history(limit=thread_count * per_thread + 10)
        self.assertEqual(len(history), thread_count * per_thread)


class SwitchDatabaseTests(MemoryManagerTestCase):
    """F2.7 (adozione, seconda fetta - isolamento vero di memoria/cronologia per profilo):
    switch_database() ripunta la STESSA istanza a un altro file, senza dover ricostruire i
    collaboratori (WorkflowManager/TriggerManager/ProcedureManager/ContactBook/skill di memoria)
    che gia' ne tengono un riferimento fisso dalla loro costruzione. Prova diretta del
    ragionamento di sicurezza in ROADMAP_EXECUTION.md: ogni metodo pubblico passa dallo stesso
    RLock prima di toccare self._connection, quindi lo scambio e' visibile atomicamente."""

    def test_data_written_before_the_switch_is_not_visible_after(self):
        self.manager.set_preference("citta", "Roma")
        other_path = self.tmp_dir / "altro-profilo.db"

        self.manager.switch_database(other_path)

        self.assertIsNone(self.manager.get_preference("citta"))

    def test_data_written_after_the_switch_lands_in_the_new_file_not_the_old_one(self):
        old_path = self.manager.db_path
        new_path = self.tmp_dir / "altro-profilo.db"
        self.manager.switch_database(new_path)

        self.manager.set_preference("citta", "Milano")

        reopened_old = MemoryManager(db_path=old_path)
        self.addCleanup(reopened_old.close)
        self.assertIsNone(reopened_old.get_preference("citta"))

    def test_switching_back_to_the_original_file_finds_its_data_intact(self):
        original_path = self.manager.db_path
        self.manager.set_preference("citta", "Roma")
        self.manager.switch_database(self.tmp_dir / "altro-profilo.db")
        self.manager.set_preference("citta", "Milano")

        self.manager.switch_database(original_path)

        self.assertEqual(self.manager.get_preference("citta"), "Roma")

    def test_the_db_path_attribute_reflects_the_active_database(self):
        new_path = self.tmp_dir / "altro-profilo.db"
        self.manager.switch_database(new_path)
        self.assertEqual(self.manager.db_path, new_path)

    def test_the_old_connection_is_really_closed_not_leaked(self):
        """Se la vecchia connessione restasse aperta, il file non sarebbe eliminabile su Windows
        (un handle aperto blocca la cancellazione) - la prova piu' diretta che close() e' stato
        chiamato per davvero, non solo che il nuovo file funziona."""
        old_path = self.manager.db_path
        self.manager.set_preference("citta", "Roma")
        self.manager.switch_database(self.tmp_dir / "altro-profilo.db")

        old_path.unlink()  # solleverebbe PermissionError su Windows se la connessione fosse ancora aperta

        self.assertFalse(old_path.exists())

    def test_a_dependent_holding_a_fixed_reference_follows_the_switch_automatically(self):
        """Il punto centrale di F2.7: TriggerManager (come WorkflowManager/ProcedureManager/
        ContactBook/le skill di memoria in produzione) tiene un riferimento FISSO a questa
        istanza dalla propria costruzione, mai ricostruito - qui si dimostra che vede il nuovo
        database SENZA essere toccato in alcun modo."""
        from core.trigger_manager import TriggerManager
        from core.workflow_manager import WorkflowManager

        workflow_manager = WorkflowManager(self.manager)
        trigger_manager = TriggerManager(self.manager, workflow_manager)
        trigger_manager.save("promemoria-latte", "qualunque", "time", {"at": "09:00"})

        self.manager.switch_database(self.tmp_dir / "altro-profilo.db")

        self.assertEqual(trigger_manager.list_all(), [])
        trigger_manager.save("promemoria-pane", "qualunque", "time", {"at": "10:00"})
        self.assertEqual(len(trigger_manager.list_all()), 1)
        self.assertEqual(trigger_manager.list_all()[0]["name"], "promemoria-pane")

    def test_a_migration_failure_on_the_new_path_leaves_the_old_connection_still_usable(self):
        """Un file nuovo corrotto (qui: una cartella al posto di un file, cosi' sqlite3.connect
        stesso fallisce) non deve MAI lasciare l'istanza senza alcuna connessione valida - i dati
        gia' presenti restano leggibili/scrivibili sul database originale."""
        self.manager.set_preference("citta", "Roma")
        broken_path = self.tmp_dir / "non-un-file"
        broken_path.mkdir()

        with self.assertRaises(sqlite3.OperationalError):
            self.manager.switch_database(broken_path)

        self.assertEqual(self.manager.get_preference("citta"), "Roma")
        self.manager.set_preference("altra-chiave", "ancora funzionante")
        self.assertEqual(self.manager.get_preference("altra-chiave"), "ancora funzionante")

    def test_concurrent_remember_calls_during_a_switch_never_corrupt_either_database(self):
        """Thread veri: un thread scrive senza sosta mentre un altro esegue lo switch - ogni
        singola remember() deve finire per intero PRIMA o DOPO lo switch (mai a cavallo, mai
        un'eccezione non gestita), grazie allo stesso RLock gia' usato da ogni metodo pubblico."""
        new_path = self.tmp_dir / "altro-profilo.db"
        errors = []
        stop = threading.Event()

        def _remember_forever():
            i = 0
            while not stop.is_set():
                try:
                    self.manager.remember(f"chiave-{i}", f"valore-{i}")
                except Exception as exc:  # pragma: no cover - fallirebbe il test comunque
                    errors.append(exc)
                i += 1

        # daemon=True: se switch_database() sollevasse (es. contro il codice precedente a questo
        # incremento, che non ha ancora il metodo) il test non deve restare appeso per sempre in
        # attesa di un thread scrittore che nessuno fermerebbe piu' - stesso principio "un test
        # non deve mai bloccare l'intera suite" gia' seguito altrove in questo progetto.
        writer = threading.Thread(target=_remember_forever, daemon=True)
        writer.start()
        try:
            self.manager.switch_database(new_path)
        finally:
            stop.set()
            writer.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertEqual(self.manager.db_path, new_path)


if __name__ == "__main__":
    unittest.main()
