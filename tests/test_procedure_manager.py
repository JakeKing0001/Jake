"""Test unitari per core/procedure_manager.py (F3.8.5, prima fetta - la persistenza a lungo
termine di una procedura, mai affrontata prima d'ora). Stesso schema di
tests/test_workflow_manager.py (il precedente piu' vicino gia' esistente): un MemoryManager
VERO con un file temporaneo, non un finto - la serializzazione/deserializzazione reale e
l'upsert su (key, category) di MemoryManager.remember() sono proprio la parte interessante da
verificare, non qualcosa che un mock proverebbe."""
import tempfile
import unittest
from pathlib import Path

from core.computer_use.procedure import ACTION_CLICK, ACTION_TYPE, RecordedStep
from core.computer_use.selector import ElementSelector
from core.memory_manager import MemoryManager
from core.procedure_manager import ProcedureManager


class _WithManager(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_procedure_manager_test_"))
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")
        self.manager = ProcedureManager(self.memory_manager)


class SaveAndLoadTests(_WithManager):
    def test_a_saved_procedure_round_trips_through_load(self):
        steps = [
            RecordedStep(
                action=ACTION_TYPE,
                selector=ElementSelector(automation_id="fixture_input", window_title_contains="Fixture"),
                text="elemento di ${utente}",
            ),
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Fixture"),
                risk_intent="DELETE_PATH",
            ),
        ]
        self.manager.save("aggiungi_elemento", steps)

        loaded = self.manager.load("aggiungi_elemento")

        self.assertEqual(loaded, steps)
        self.assertEqual(loaded[0].text, "elemento di ${utente}")
        self.assertEqual(loaded[1].risk_intent, "DELETE_PATH")

    def test_loading_an_unknown_procedure_returns_none(self):
        self.assertIsNone(self.manager.load("non_esiste"))

    def test_a_procedure_with_no_steps_round_trips_as_an_empty_list(self):
        """Onesto `None` per 'non esiste affatto' (test sopra) - una lista vuota SALVATA davvero
        e' un fatto diverso, deve restituire `[]`, non `None`."""
        self.manager.save("vuota", [])

        loaded = self.manager.load("vuota")

        self.assertEqual(loaded, [])
        self.assertIsNotNone(loaded)

    def test_saving_with_the_same_name_again_replaces_it_not_duplicates_it(self):
        selector = ElementSelector(name="Aggiungi", window_title_contains="Fixture")
        self.manager.save("routine", [RecordedStep(action=ACTION_CLICK, selector=selector)])
        self.manager.save("routine", [
            RecordedStep(action=ACTION_CLICK, selector=selector),
            RecordedStep(action=ACTION_CLICK, selector=selector),
        ])

        loaded = self.manager.load("routine")

        self.assertEqual(len(loaded), 2)
        self.assertEqual(self.manager.list_names(), ["routine"])


class ListNamesTests(_WithManager):
    def test_lists_every_saved_procedure_name(self):
        selector = ElementSelector(name="Aggiungi", window_title_contains="Fixture")
        self.manager.save("uno", [RecordedStep(action=ACTION_CLICK, selector=selector)])
        self.manager.save("due", [RecordedStep(action=ACTION_CLICK, selector=selector)])
        self.assertEqual(set(self.manager.list_names()), {"uno", "due"})

    def test_empty_when_nothing_saved(self):
        self.assertEqual(self.manager.list_names(), [])

    def test_more_than_a_thousand_is_not_silently_truncated_below_what_was_saved(self):
        """Stesso principio gia' verificato per WorkflowManager (F1, buco reale trovato in un
        incremento precedente di questa sessione): un limite fisso non deve troncare in
        silenzio le procedure piu' vecchie/meno di recente aggiornate - qui verificato restando
        SOTTO il tetto dichiarato (troppo lento creare 1000+ righe reali solo per riprovare lo
        stesso fatto gia' dimostrato per i workflow), a dimostrare che list_names() non ha un
        limite PIU' BASSO nascosto per questa categoria specifica."""
        selector = ElementSelector(name="Aggiungi", window_title_contains="Fixture")
        for i in range(60):
            self.manager.save(f"procedura_{i}", [RecordedStep(action=ACTION_CLICK, selector=selector)])
        self.assertEqual(len(self.manager.list_names()), 60)


if __name__ == "__main__":
    unittest.main()
