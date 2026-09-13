"""Test unitari per core/trigger_manager.py: nessuna suite esisteva finora. Usa un MemoryManager
vero con file temporaneo e un WorkflowManager vero (stesso motore di persistenza reale, categoria
dedicata 'trigger') - un trigger e' un record JSON qualsiasi nella stessa memoria a lungo
termine usata da REMEMBER/RECALL/FORGET, quindi e' proprio l'interazione reale con
MemoryManager.remember()/recall()/forget() a essere la parte interessante da verificare.

F1 (indiretto): buco reale trovato e corretto in questa sessione. list_all() aveva un limite
fisso di 50 risultati - core/trigger_scheduler.py itera list_all() a OGNI ciclo di controllo per
decidere cosa far scattare, quindi un utente con piu' di 50 trigger avrebbe visto i trigger piu'
vecchi/meno di recente aggiornati smettere di scattare mai piu' superata quella soglia, in
silenzio."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core.memory_manager import MemoryManager
from core.planner import Plan
from core.trigger_manager import TriggerManager
from core.workflow_manager import WorkflowManager


class _WithManager(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_trigger_manager_test_"))
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")
        self.workflow_manager = WorkflowManager(self.memory_manager)
        self.manager = TriggerManager(self.memory_manager, self.workflow_manager)


class SaveAndListTests(_WithManager):
    def test_a_saved_trigger_appears_in_list_all_with_its_name(self):
        self.manager.save("promemoria mattutino", "routine_mattina", "time", {"at": "08:00"})

        [trigger] = self.manager.list_all()

        self.assertEqual(trigger["name"], "promemoria mattutino")
        self.assertEqual(trigger["workflow_name"], "routine_mattina")
        self.assertEqual(trigger["type"], "time")
        self.assertEqual(trigger["spec"], {"at": "08:00"})
        self.assertIsNone(trigger["last_fired"])

    def test_list_all_is_empty_when_nothing_saved(self):
        self.assertEqual(self.manager.list_all(), [])

    def test_saving_with_the_same_name_again_replaces_it_not_duplicates_it(self):
        self.manager.save("t", "wf1", "time", {"at": "08:00"})
        self.manager.save("t", "wf2", "time", {"at": "09:00"})

        triggers = self.manager.list_all()

        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["workflow_name"], "wf2")

    def test_more_than_fifty_triggers_are_all_listed(self):
        """Il buco reale: list_all() aveva un limite fisso di 50, e TriggerScheduler la itera a
        ogni ciclo per decidere cosa far scattare - un trigger fuori dai primi 50 non avrebbe
        piu' scattato mai."""
        for i in range(60):
            self.manager.save(f"trigger_{i}", "wf", "time", {"at": "08:00"})
        self.assertEqual(len(self.manager.list_all()), 60)


class MarkFiredTests(_WithManager):
    def test_mark_fired_records_the_timestamp(self):
        self.manager.save("t", "wf", "time", {"at": "08:00"})
        self.manager.mark_fired("t", "2026-09-10T08:00:00+00:00")

        [trigger] = self.manager.list_all()
        self.assertEqual(trigger["last_fired"], "2026-09-10T08:00:00+00:00")

    def test_mark_fired_preserves_the_rest_of_the_record(self):
        self.manager.save("t", "wf", "time", {"at": "08:00"})
        self.manager.mark_fired("t", "2026-09-10T08:00:00+00:00")

        [trigger] = self.manager.list_all()
        self.assertEqual(trigger["workflow_name"], "wf")
        self.assertEqual(trigger["spec"], {"at": "08:00"})

    def test_mark_fired_on_an_unknown_trigger_does_nothing_and_does_not_raise(self):
        self.manager.mark_fired("non_esiste", "2026-09-10T08:00:00+00:00")
        self.assertEqual(self.manager.list_all(), [])


class DeleteAndWorkflowExistsTests(_WithManager):
    def test_delete_removes_the_trigger_and_returns_true(self):
        self.manager.save("t", "wf", "time", {"at": "08:00"})
        self.assertTrue(self.manager.delete("t"))
        self.assertEqual(self.manager.list_all(), [])

    def test_delete_returns_false_when_nothing_matches(self):
        self.assertFalse(self.manager.delete("non_esiste"))

    def test_workflow_exists_is_true_for_a_saved_workflow(self):
        self.workflow_manager.save("wf", Plan(steps=[]))
        self.assertTrue(self.manager.workflow_exists("wf"))

    def test_workflow_exists_is_false_for_an_unknown_workflow(self):
        self.assertFalse(self.manager.workflow_exists("non_esiste"))

    def test_default_workflow_manager_is_created_when_none_is_passed(self):
        manager = TriggerManager(self.memory_manager)
        self.assertIsInstance(manager.workflow_manager, WorkflowManager)


class ConcurrentMarkFiredTests(_WithManager):
    """F1.8.7 ("testare race su trigger"): mark_fired() (chiamato da TriggerScheduler su un
    thread separato) faceva una lettura e una scrittura come due chiamate separate a
    memory_manager - vedi il docstring di mark_fired() per il buco reale. A differenza degli
    altri buchi di questa sessione, qui sys.setswitchinterval()+Barrier da soli riproducono la
    corsa solo raramente e in modo incostante (finestra stretta, come per
    DeviceRegistry.claim()): questo test forza invece l'interleaving esatto con due
    threading.Event, cosi' da essere deterministico invece di probabilistico."""

    def test_a_concurrent_save_is_not_silently_lost_by_mark_fired(self):
        self.manager.save("buonanotte", "vecchia_automazione", "time", {"at": "22:00"})

        record_read = threading.Event()
        proceed_to_write = threading.Event()
        real_load_raw = self.manager._load_raw

        def _load_raw_then_wait(name):
            record = real_load_raw(name)
            record_read.set()
            proceed_to_write.wait(timeout=5)
            return record

        def _scheduler_fires():
            with mock.patch.object(self.manager, "_load_raw", side_effect=_load_raw_then_wait):
                self.manager.mark_fired("buonanotte", "2026-09-13T22:00:00+00:00")

        fire_thread = threading.Thread(target=_scheduler_fires)
        fire_thread.start()
        self.assertTrue(record_read.wait(timeout=5), "mark_fired non ha letto il record in tempo")

        edit_thread = threading.Thread(
            target=self.manager.save, args=("buonanotte", "nuova_automazione", "time", {"at": "23:00"})
        )
        edit_thread.start()
        # Senza il fix, save() non e' bloccata da nessun lock e fa in tempo a completare qui,
        # PRIMA che mark_fired() scriva sopra con il record vecchio gia' letto - questo e'
        # esattamente il buco. Con il fix, save() resta bloccata su memory_manager.lock finche'
        # mark_fired() non lo rilascia, quindi questa pausa non le basta per completare.
        time.sleep(0.05)

        proceed_to_write.set()
        fire_thread.join(timeout=5)
        edit_thread.join(timeout=5)

        [trigger] = self.manager.list_all()
        self.assertEqual(
            trigger["workflow_name"], "nuova_automazione",
            "la modifica concorrente dell'utente e' stata sovrascritta da mark_fired()",
        )


if __name__ == "__main__":
    unittest.main()
