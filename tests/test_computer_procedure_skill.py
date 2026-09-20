"""Test per skills/computer_procedure.py (RunComputerProcedureSkill) - l'esatto analogo di
RunWorkflowSkill per una procedura di Computer Use (F3.8) invece che per un'automazione di
skill. Test end-to-end reali contro la fixture Qt gia' esistente (F3.1.1), stesso principio
"prova il percorso vero, non solo i pezzi" gia' seguito da tests/test_procedure.py: una
procedura salvata con ProcedureManager (memoria vera, file temporaneo) DAVVERO caricata e
rigiocata contro un'app vera, non un RecordedStep costruito a mano nel test."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.computer_agent import ComputerAgent
from core.computer_use.procedure import ACTION_CLICK, ACTION_TYPE, RecordedStep
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter
from core.memory_manager import MemoryManager
from core.procedure_manager import ProcedureManager
from skills.computer_procedure import RunComputerProcedureSkill

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_WINDOW_TITLE = "Jake Computer Use Fixture"


class MissingParametersTests(unittest.TestCase):
    """Nessuna app/procedura reale necessaria - il controllo dei parametri avviene PRIMA di
    toccare ProcedureManager/ComputerAgent."""

    def test_a_missing_name_is_reported(self):
        skill = RunComputerProcedureSkill(procedure_manager=None)

        result = skill.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class ProcedureLookupTests(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_computer_procedure_skill_test_"))
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")
        self.procedure_manager = ProcedureManager(self.memory_manager)
        self.skill = RunComputerProcedureSkill(self.procedure_manager)

    def test_an_unknown_procedure_name_is_reported(self):
        result = self.skill.execute({"name": "non_esiste"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_an_empty_saved_procedure_is_reported(self):
        self.procedure_manager.save("vuota", [])

        result = self.skill.execute({"name": "vuota"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "EMPTY_PROCEDURE")


class RunAgainstTheRealFixtureTests(unittest.TestCase):
    """End-to-end reale: un processo fixture DEDICATO per test (la procedura muta lo stato -
    aggiunge un elemento alla lista - stesso principio gia' seguito da
    tests/test_procedure.py::ReplayAgainstTheRealFixtureTests)."""

    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        self.addCleanup(self._terminate_process)
        self.adapter = UIAutomationAdapter()
        self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)  # attende che sia visibile

        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_computer_procedure_skill_e2e_"))
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")
        self.procedure_manager = ProcedureManager(self.memory_manager)
        self.skill = RunComputerProcedureSkill(self.procedure_manager, computer_agent=ComputerAgent())

    def _terminate_process(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def _save_add_item_procedure(self, name: str, text: str = "elemento dalla skill") -> None:
        self.procedure_manager.save(name, [
            RecordedStep(
                action=ACTION_TYPE,
                selector=ElementSelector(
                    automation_id="QApplication.jake_fixture_window.fixture_input",
                    window_title_contains="Computer Use Fixture",
                ),
                text=text,
            ),
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
            ),
        ])

    def test_a_saved_procedure_replays_for_real_through_the_skill(self):
        self._save_add_item_procedure("aggiungi_elemento")

        result = self.skill.execute({"name": "aggiungi_elemento"})

        self.assertTrue(result.success, result)
        self.assertEqual(result.data["completed_steps"], 2)
        self.assertEqual(result.data["total_steps"], 2)

        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        engine = SelectorEngine(self.adapter)
        item = engine.find_unique(window, ElementSelector(name="elemento dalla skill", control_type="ListItem"))
        self.assertEqual(item.name, "elemento dalla skill")

    def test_parameters_are_substituted_through_the_skill(self):
        self.procedure_manager.save("aggiungi_parametrico", [
            RecordedStep(
                action=ACTION_TYPE,
                selector=ElementSelector(
                    automation_id="QApplication.jake_fixture_window.fixture_input",
                    window_title_contains="Computer Use Fixture",
                ),
                text="elemento di ${utente}",
            ),
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
            ),
        ])

        result = self.skill.execute({"name": "aggiungi_parametrico", "parameters": {"utente": "Jake"}})

        self.assertTrue(result.success, result)
        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        engine = SelectorEngine(self.adapter)
        item = engine.find_unique(window, ElementSelector(name="elemento di Jake", control_type="ListItem"))
        self.assertEqual(item.name, "elemento di Jake")

    def test_dry_run_never_touches_the_app_for_real(self):
        self._save_add_item_procedure("aggiungi_elemento")

        result = self.skill.execute({"name": "aggiungi_elemento", "dry_run": True})

        self.assertTrue(result.success, result)
        self.assertTrue(result.data["dry_run"])
        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        engine = SelectorEngine(self.adapter)
        item_list = engine.find_unique_element(
            window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_list"),
        )
        self.assertEqual(engine.find_all(item_list, ElementSelector(control_type="ListItem")), [])

    def test_a_policy_engine_assigned_after_construction_is_honored(self):
        """Stesso identico schema gia' usato da JakeCore per RUN_WORKFLOW: `skill.policy_engine =
        ...` assegnato DOPO __init__ (mai passato al costruttore) - il blocco deve raggiungere
        DAVVERO l'azione, non solo essere salvato su un attributo inerte."""
        from core.policy_engine import PolicyEngine

        self.procedure_manager.save("elimina", [
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
                risk_intent="DELETE_PATH",
            ),
        ])
        self.skill.policy_engine = PolicyEngine(blocked_intents={"DELETE_PATH"})

        result = self.skill.execute({"name": "elimina"})

        self.assertFalse(result.success)
        self.assertEqual(result.data["last_error"], "POLICY_BLOCKED")
        self.assertFalse(result.data["likely_drift"], "un blocco di policy non e' un cambiamento strutturale dell'app")
        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        engine = SelectorEngine(self.adapter)
        item_list = engine.find_unique_element(
            window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_list"),
        )
        self.assertEqual(engine.find_all(item_list, ElementSelector(control_type="ListItem")), [], "il click bloccato non deve mai raggiungere l'app")

    def test_a_selector_that_no_longer_resolves_is_reported_as_likely_drift(self):
        """F3.8.6 (adozione): una procedura registrata contro un nome di bottone che NON esiste
        (mai esistito, non solo "cambiato") si comporta come l'app fosse cambiata struttura da
        quando la procedura e' stata salvata - il caso motivante di `is_likely_drift`."""
        self.procedure_manager.save("bottone_fantasma", [
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(name="Questo bottone non esiste XYZ", window_title_contains="Computer Use Fixture"),
            ),
        ])

        result = self.skill.execute({"name": "bottone_fantasma"})

        self.assertFalse(result.success)
        self.assertEqual(result.data["last_error"], "NOT_FOUND")
        self.assertTrue(result.data["likely_drift"])


if __name__ == "__main__":
    unittest.main()
