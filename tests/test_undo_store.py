"""Test unitari per core/undo_store.py (F1.3.5, "generare undo token con scadenza e
precondizioni" - vedi il docstring del modulo per il buco che chiude: UndoDescriptor esisteva
solo come contratto dati, mai costruito in nessun punto di produzione)."""
import threading
import unittest

from core.undo_store import DEFAULT_UNDO_TTL_SECONDS, UndoStore, generate_undo_descriptor


class GenerateUndoDescriptorTests(unittest.TestCase):
    def test_create_path_produces_a_delete_path_undo(self):
        descriptor = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "C:\\nuovo.txt"})

        self.assertIsNotNone(descriptor)
        self.assertEqual(descriptor.action_id, "action-1")
        self.assertEqual(descriptor.compensating_intent, "DELETE_PATH")
        self.assertEqual(descriptor.compensating_parameters, {"path": "C:\\nuovo.txt", "confirmed": True})

    def test_rename_path_produces_a_rename_back_undo(self):
        descriptor = generate_undo_descriptor(
            "action-2", "RENAME_PATH", {"path": "C:\\vecchio.txt", "new_path": "C:\\nuovo.txt"},
        )

        self.assertEqual(descriptor.compensating_intent, "RENAME_PATH")
        self.assertEqual(descriptor.compensating_parameters, {"path": "C:\\nuovo.txt", "new_name": "vecchio.txt"})

    def test_move_path_produces_a_move_back_undo(self):
        descriptor = generate_undo_descriptor(
            "action-3", "MOVE_PATH", {"path": "C:\\origine\\file.txt", "new_path": "C:\\dest\\file.txt"},
        )

        self.assertEqual(descriptor.compensating_intent, "MOVE_PATH")
        self.assertEqual(
            descriptor.compensating_parameters,
            {"path": "C:\\dest\\file.txt", "destination": "C:\\origine", "confirmed": True},
        )

    def test_extract_archive_produces_a_delete_path_undo(self):
        descriptor = generate_undo_descriptor("action-4", "EXTRACT_ARCHIVE", {"destination": "C:\\estratto"})

        self.assertEqual(descriptor.compensating_intent, "DELETE_PATH")
        self.assertEqual(descriptor.compensating_parameters, {"path": "C:\\estratto", "confirmed": True})

    def test_an_intent_without_a_natural_inverse_produces_no_descriptor(self):
        """DELETE_PATH non ha un inverso naturale (cancellare non si annulla, vedi
        INTENT_SAFETY_REGISTRY) - None, non un valore indovinato."""
        self.assertIsNone(generate_undo_descriptor("action-5", "DELETE_PATH", {"path": "x"}))

    def test_an_intent_never_censited_at_all_produces_no_descriptor(self):
        self.assertIsNone(generate_undo_descriptor("action-6", "GET_TIME", {}))

    def test_malformed_data_missing_expected_keys_produces_no_descriptor_not_a_crash(self):
        """Onesto 'non posso generarlo' invece di sollevare KeyError verso un chiamante che non
        si aspetta un'eccezione da qui - un result malformato non deve mai far cadere il
        chokepoint che genera l'undo come effetto collaterale di un'azione gia' riuscita."""
        self.assertIsNone(generate_undo_descriptor("action-7", "CREATE_PATH", {}))

    def test_expires_at_is_now_plus_the_ttl(self):
        descriptor = generate_undo_descriptor(
            "action-8", "CREATE_PATH", {"path": "x"}, ttl_seconds=100, now=1000.0,
        )
        self.assertEqual(descriptor.expires_at, 1100.0)

    def test_default_ttl_is_five_minutes(self):
        self.assertEqual(DEFAULT_UNDO_TTL_SECONDS, 5 * 60)

    def test_preconditions_are_deliberately_none(self):
        """F1.1.7 gia' rifiutato lo stesso giudizio caso per caso per ActionProposal.preconditions
        - non inventato nemmeno qui."""
        descriptor = generate_undo_descriptor("action-9", "CREATE_PATH", {"path": "x"})
        self.assertIsNone(descriptor.preconditions)

    def test_a_freshly_generated_descriptor_is_usable(self):
        descriptor = generate_undo_descriptor("action-10", "CREATE_PATH", {"path": "x"})
        self.assertTrue(descriptor.is_usable())


class UndoStoreTests(unittest.TestCase):
    def test_a_saved_descriptor_can_be_retrieved_by_action_id(self):
        store = UndoStore()
        descriptor = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"})

        store.save(descriptor)

        self.assertIs(store.get("action-1"), descriptor)

    def test_an_unknown_action_id_returns_none(self):
        store = UndoStore()
        self.assertIsNone(store.get("non-esiste"))

    def test_an_expired_descriptor_is_not_returned(self):
        store = UndoStore()
        descriptor = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"}, ttl_seconds=10, now=1000.0)
        store.save(descriptor)

        self.assertIsNone(store.get("action-1", now=1011.0), "scaduto un secondo fa")
        self.assertIsNotNone(store.get("action-1", now=1009.0), "non ancora scaduto")

    def test_mark_used_consumes_the_undo(self):
        store = UndoStore()
        descriptor = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"})
        store.save(descriptor)

        consumed = store.mark_used("action-1")

        self.assertTrue(consumed)
        self.assertIsNone(store.get("action-1"), "un undo gia' consumato non deve poter essere usato due volte")

    def test_mark_used_on_an_already_used_descriptor_returns_false(self):
        store = UndoStore()
        store.save(generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"}))
        store.mark_used("action-1")

        self.assertFalse(store.mark_used("action-1"), "gia' consumato, non c'e' nulla da consumare una seconda volta")

    def test_mark_used_on_an_unknown_action_id_returns_false(self):
        store = UndoStore()
        self.assertFalse(store.mark_used("non-esiste"))

    def test_mark_used_on_an_expired_descriptor_returns_false(self):
        store = UndoStore()
        descriptor = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"}, ttl_seconds=10, now=1000.0)
        store.save(descriptor)

        self.assertFalse(store.mark_used("action-1"))

    def test_saving_a_new_descriptor_for_the_same_action_id_replaces_the_old_one(self):
        store = UndoStore()
        store.save(generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "primo"}))
        store.save(generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "secondo"}))

        self.assertEqual(store.get("action-1").compensating_parameters["path"], "secondo")

    def test_concurrent_saves_and_reads_from_real_threads_never_corrupt_the_store(self):
        """Thread veri, stesso principio gia' usato altrove in questa sessione per i buchi di
        concorrenza su uno store condiviso: ogni thread salva e poi rilegge SUBITO il proprio
        descrittore, mai quello di un altro thread."""
        store = UndoStore()
        errors: list = []
        errors_lock = threading.Lock()

        def _save_and_check(index: int):
            action_id = f"action-{index}"
            descriptor = generate_undo_descriptor(action_id, "CREATE_PATH", {"path": f"file-{index}.txt"})
            store.save(descriptor)
            retrieved = store.get(action_id)
            if retrieved is None or retrieved.compensating_parameters["path"] != f"file-{index}.txt":
                with errors_lock:
                    errors.append(index)

        threads = [threading.Thread(target=_save_and_check, args=(i,)) for i in range(100)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
