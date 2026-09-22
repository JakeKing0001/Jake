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


class TenAdditionalReversibleActionPairsTests(unittest.TestCase):
    """F1.3 (criterio di uscita, '80% delle azioni reversibili dispone di undo testato'): le dieci
    coppie aggiunte a core/execution_safety.py oltre alle quattro filesystem gia' esistenti - vedi
    il commento sopra UNDO_PARAMS_BY_INTENT li' per il criterio di selezione/esclusione applicato
    a tutte le 67 intent LOCAL_REVERSIBLE (core/risk.py)."""

    def test_add_todo_produces_a_delete_todo_undo(self):
        descriptor = generate_undo_descriptor("a1", "ADD_TODO", {"text": "compra il latte"})
        self.assertEqual(descriptor.compensating_intent, "DELETE_TODO")
        self.assertEqual(descriptor.compensating_parameters, {"text": "compra il latte"})

    def test_set_reminder_produces_a_delete_reminder_undo(self):
        descriptor = generate_undo_descriptor("a2", "SET_REMINDER", {"text": "chiamare mamma", "due_at_local": "18:00"})
        self.assertEqual(descriptor.compensating_intent, "DELETE_REMINDER")
        self.assertEqual(descriptor.compensating_parameters, {"text": "chiamare mamma"})

    def test_set_daily_reminder_produces_a_delete_reminder_undo(self):
        descriptor = generate_undo_descriptor("a3", "SET_DAILY_REMINDER", {"text": "stretching", "at_time": "07:00"})
        self.assertEqual(descriptor.compensating_intent, "DELETE_REMINDER")
        self.assertEqual(descriptor.compensating_parameters, {"text": "stretching"})

    def test_set_timer_produces_a_cancel_timer_undo_keyed_by_label(self):
        descriptor = generate_undo_descriptor(
            "a4", "SET_TIMER", {"seconds_total": 300, "duration": "5 minuti", "label": "pasta", "due_at_local": "12:05:00"},
        )
        self.assertEqual(descriptor.compensating_intent, "CANCEL_TIMER")
        self.assertEqual(descriptor.compensating_parameters, {"label": "pasta"})

    def test_start_pomodoro_produces_a_parameterless_stop_pomodoro_undo(self):
        descriptor = generate_undo_descriptor("a5", "START_POMODORO", {"minutes": 25})
        self.assertEqual(descriptor.compensating_intent, "STOP_POMODORO")
        self.assertEqual(descriptor.compensating_parameters, {})

    def test_maximize_window_produces_a_restore_window_undo(self):
        descriptor = generate_undo_descriptor("a6", "MAXIMIZE_WINDOW", {"title": "Blocco note"})
        self.assertEqual(descriptor.compensating_intent, "RESTORE_WINDOW")
        self.assertEqual(descriptor.compensating_parameters, {"title": "Blocco note"})

    def test_minimize_window_produces_a_restore_window_undo(self):
        descriptor = generate_undo_descriptor("a7", "MINIMIZE_WINDOW", {"title": "Calcolatrice"})
        self.assertEqual(descriptor.compensating_intent, "RESTORE_WINDOW")
        self.assertEqual(descriptor.compensating_parameters, {"title": "Calcolatrice"})

    def test_take_screenshot_produces_a_delete_path_undo(self):
        descriptor = generate_undo_descriptor("a8", "TAKE_SCREENSHOT", {"path": "C:\\screenshots\\s1.png"})
        self.assertEqual(descriptor.compensating_intent, "DELETE_PATH")
        self.assertEqual(descriptor.compensating_parameters, {"path": "C:\\screenshots\\s1.png", "confirmed": True})

    def test_duplicate_file_produces_a_delete_path_undo_targeting_the_copy_not_the_original(self):
        descriptor = generate_undo_descriptor(
            "a9", "DUPLICATE_FILE", {"path": "C:\\doc.txt", "destination": "C:\\doc - copia.txt"},
        )
        self.assertEqual(descriptor.compensating_intent, "DELETE_PATH")
        self.assertEqual(descriptor.compensating_parameters, {"path": "C:\\doc - copia.txt", "confirmed": True})

    def test_toggle_dark_mode_produces_a_toggle_back_undo(self):
        descriptor = generate_undo_descriptor("a10", "TOGGLE_DARK_MODE", {"enabled": True})
        self.assertEqual(descriptor.compensating_intent, "TOGGLE_DARK_MODE")
        self.assertEqual(descriptor.compensating_parameters, {"enabled": False})

    def test_toggle_dark_mode_undo_inverts_the_other_direction_too(self):
        descriptor = generate_undo_descriptor("a11", "TOGGLE_DARK_MODE", {"enabled": False})
        self.assertEqual(descriptor.compensating_parameters, {"enabled": True})

    def test_upsert_backed_actions_deliberately_have_no_undo(self):
        """REMEMBER/SET_TRIGGER/LEARN_COMMAND poggiano su un upsert (core/memory_manager.py::
        remember o core/nlu/examples.py::add_learned) - un undo-by-delete cancellerebbe una voce
        PRECEDENTE all'azione da annullare se la chiave esisteva gia'. Nessun inverso qui, per
        design, non per una dimenticanza (vedi il commento sopra UNDO_PARAMS_BY_INTENT)."""
        self.assertIsNone(generate_undo_descriptor("a12", "REMEMBER", {"key": "k", "value": "v"}))
        self.assertIsNone(generate_undo_descriptor(
            "a13", "SET_TRIGGER", {"name": "n", "workflow_name": "w"},
        ))
        self.assertIsNone(generate_undo_descriptor("a14", "LEARN_COMMAND", {"phrase": "p", "intent": "GET_TIME"}))

    def test_overwrite_prone_actions_deliberately_have_no_undo(self):
        """COMPRESS_PATH (shutil.make_archive) ed EXPORT_NOTES (Path.write_text) sovrascrivono
        incondizionatamente un file che avesse gia' quel nome/percorso - stesso motivo delle
        azioni upsert sopra, verificato leggendo il codice delle skill."""
        self.assertIsNone(generate_undo_descriptor("a15", "COMPRESS_PATH", {"path": "x", "archive_path": "x.zip"}))
        self.assertIsNone(generate_undo_descriptor("a16", "EXPORT_NOTES", {"path": "x.txt"}))

    def test_set_private_mode_deliberately_has_no_undo(self):
        """Vedi il commento sopra UNDO_PARAMS_BY_INTENT in core/execution_safety.py: l'inverso
        sarebbe meccanicamente pulito, ma renderebbe la modalita' privata riattivabile in automatico
        dal rollback di un compito interrotto - non un default sicuro per un controllo di privacy."""
        self.assertIsNone(generate_undo_descriptor("a17", "SET_PRIVATE_MODE", {"enabled": True}))

    def test_actions_with_no_reachable_inverse_skill_have_no_undo(self):
        """SET_WINDOW_ALWAYS_ON_TOP (nessuna skill toglie il flag) e SNAP_WINDOW_LEFT/RIGHT
        (data={}, nessun titolo da passare a RESTORE_WINDOW)."""
        self.assertIsNone(generate_undo_descriptor("a18", "SET_WINDOW_ALWAYS_ON_TOP", {"title": "x"}))
        self.assertIsNone(generate_undo_descriptor("a19", "SNAP_WINDOW_LEFT", {}))
        self.assertIsNone(generate_undo_descriptor("a20", "SNAP_WINDOW_RIGHT", {}))


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


class MostRecentUsableTests(unittest.TestCase):
    """F1.3.5 (skill 'annulla', consumatore): 'annulla l'ultima azione' e' per l'utente un
    concetto UNICO tra i tre chokepoint (comando diretto/agente/piano), non tre code separate -
    most_recent_usable() cerca nell'INTERO store, non per action_id specifico."""

    def test_empty_store_has_nothing_to_undo(self):
        self.assertIsNone(UndoStore().most_recent_usable())

    def test_returns_the_only_descriptor_when_there_is_one(self):
        store = UndoStore()
        descriptor = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"})
        store.save(descriptor)

        self.assertIs(store.most_recent_usable(), descriptor)

    def test_returns_the_most_recently_saved_one_not_the_oldest(self):
        store = UndoStore()
        store.save(generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "primo"}))
        second = generate_undo_descriptor("action-2", "CREATE_PATH", {"path": "secondo"})
        store.save(second)

        self.assertIs(store.most_recent_usable(), second)

    def test_skips_expired_descriptors_to_find_an_older_still_usable_one(self):
        store = UndoStore()
        still_usable = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "vivo"}, ttl_seconds=100, now=1000.0)
        expired = generate_undo_descriptor("action-2", "CREATE_PATH", {"path": "scaduto"}, ttl_seconds=10, now=1000.0)
        store.save(still_usable)
        store.save(expired)

        self.assertIs(store.most_recent_usable(now=1050.0), still_usable)

    def test_skips_an_already_consumed_descriptor(self):
        store = UndoStore()
        older = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "vecchio"})
        newer = generate_undo_descriptor("action-2", "CREATE_PATH", {"path": "nuovo"})
        store.save(older)
        store.save(newer)
        store.mark_used("action-2")

        self.assertEqual(store.most_recent_usable().action_id, "action-1")

    def test_nothing_usable_returns_none_not_the_oldest_stale_one(self):
        store = UndoStore()
        store.save(generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"}, ttl_seconds=10, now=1000.0))

        self.assertIsNone(store.most_recent_usable(now=1050.0))


if __name__ == "__main__":
    unittest.main()
