"""Test unitari per core/workflow_manager.py: nessuna suite esisteva finora. Usa un
MemoryManager vero con file temporaneo (stesso motore di persistenza reale, categoria dedicata
'workflow'), non un finto - un workflow e' un Plan (v0.7) serializzato in JSON dentro un record
di memoria qualsiasi, e sono proprio la serializzazione/deserializzazione reale e l'upsert su
(key, category) di MemoryManager.remember() a essere la parte interessante da verificare.

F1 (indiretto): corretto insieme allo stesso buco in core/trigger_manager.py in questa sessione
- list_names() aveva un limite fisso di 50 risultati che avrebbe troncato silenziosamente i
workflow piu' vecchi/meno di recente aggiornati una volta superata quella soglia."""
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from core.planner import Plan, PlanStep
from core.workflow_manager import WorkflowManager


class _WithManager(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_workflow_manager_test_"))
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")
        self.manager = WorkflowManager(self.memory_manager)


class SaveAndLoadTests(_WithManager):
    def test_a_saved_workflow_round_trips_through_load(self):
        plan = Plan(steps=[
            PlanStep(intent="OPEN_APP", parameters={"app": "spotify"}, description="apri spotify"),
            PlanStep(intent="SET_VOLUME", parameters={"action": "up"}, description="alza il volume"),
        ])
        self.manager.save("morning_routine", plan)

        loaded = self.manager.load("morning_routine")

        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.steps), 2)
        self.assertEqual(loaded.steps[0].intent, "OPEN_APP")
        self.assertEqual(loaded.steps[0].parameters, {"app": "spotify"})
        self.assertEqual(loaded.steps[0].description, "apri spotify")
        self.assertEqual(loaded.steps[1].intent, "SET_VOLUME")

    def test_loading_an_unknown_workflow_returns_none(self):
        self.assertIsNone(self.manager.load("non_esiste"))

    def test_a_workflow_with_no_steps_round_trips_as_an_empty_plan(self):
        self.manager.save("vuoto", Plan(steps=[]))
        loaded = self.manager.load("vuoto")
        self.assertEqual(loaded.steps, [])

    def test_saving_with_the_same_name_again_replaces_it_not_duplicates_it(self):
        self.manager.save("routine", Plan(steps=[PlanStep(intent="OPEN_APP", parameters={"app": "a"})]))
        self.manager.save("routine", Plan(steps=[PlanStep(intent="OPEN_APP", parameters={"app": "b"})]))

        loaded = self.manager.load("routine")

        self.assertEqual(len(loaded.steps), 1)
        self.assertEqual(loaded.steps[0].parameters, {"app": "b"})
        self.assertEqual(self.manager.list_names(), ["routine"])


class ListNamesTests(_WithManager):
    def test_lists_every_saved_workflow_name(self):
        self.manager.save("uno", Plan(steps=[]))
        self.manager.save("due", Plan(steps=[]))
        self.assertEqual(set(self.manager.list_names()), {"uno", "due"})

    def test_empty_when_nothing_saved(self):
        self.assertEqual(self.manager.list_names(), [])

    def test_more_than_fifty_workflows_are_all_listed(self):
        """Il buco reale: list_names() aveva un limite fisso di 50, che avrebbe troncato
        silenziosamente i workflow piu' vecchi/meno di recente aggiornati."""
        for i in range(60):
            self.manager.save(f"workflow_{i}", Plan(steps=[]))
        self.assertEqual(len(self.manager.list_names()), 60)


if __name__ == "__main__":
    unittest.main()
