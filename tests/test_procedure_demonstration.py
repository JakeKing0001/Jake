"""F3.8 contro la fixture Qt vera: una procedura DIMOSTRATA con mouse e tastiera reali sopravvive a
riavvio dell'app, resize/spostamento e dati diversi (criterio di uscita di F3.8); un drift la
sospende invece di improvvisare; un'app diversa da quella dimostrata la sospende senza input;
l'ultima esecuzione si annulla; piu' rischio richiede una nuova approvazione.

Nessun dato personale: fixture sintetica e database di memoria temporaneo. Ogni input reale passa
dalla guardia dei test (mai fuori dalla fixture)."""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from core.computer_use.demonstration import UiaDemonstrationSampler
from core.computer_use.executor import ActionExecutor
from core.computer_use.procedure import ACTION_CLICK, ACTION_TYPE, RecordedStep
from core.computer_use.procedure_lifecycle import STATUS_SUSPENDED
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter
from core.memory_manager import MemoryManager
from core.procedure_manager import ProcedureManager
from skills.computer_procedure import RecordComputerProcedureSkill, RunComputerProcedureSkill

# Mouse e tastiera veri: mai input fuori dalla fixture (vedi tests/fixture_input_guard.py).
from tests.fixture_input_guard import install as setUpModule, uninstall as tearDownModule  # noqa: E402,F401

_ROOT = Path(__file__).resolve().parent.parent
_TITLE = "Jake Computer Use Fixture"
_INPUT = "QApplication.jake_fixture_window.fixture_input"
_LIST = "QApplication.jake_fixture_window.fixture_list"


class _FixtureCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = MemoryManager(db_path=Path(tmp.name) / "memory.db")
        self.addCleanup(memory.close)
        self.manager = ProcedureManager(memory)
        self.adapter = UIAutomationAdapter()
        self._processes = []
        self.window = self._launch()

    def tearDown(self):
        for process in self._processes:
            self._kill(process)

    def _launch(self):
        process = subprocess.Popen([sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "90"],
                                   cwd=str(_ROOT))
        self._processes.append(process)
        window = self.adapter.find_window_by_title(_TITLE, timeout_seconds=15.0)
        self.adapter.bring_to_front(window)
        return window

    @staticmethod
    def _kill(process):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _center(self, selector):
        element = SelectorEngine(self.adapter).wait_for_unique_element(self.window, selector, timeout_seconds=5.0)
        left, top, width, height = self.adapter.describe_element(element).bounds
        return left + width // 2, top + height // 2

    def _human_click(self, x, y):
        import pyautogui

        pyautogui.moveTo(x, y)
        pyautogui.mouseDown()
        time.sleep(0.12)
        pyautogui.mouseUp()
        time.sleep(0.25)

    def _list_items(self):
        engine = SelectorEngine(self.adapter)
        item_list = engine.find_unique_element(self.window, ElementSelector(automation_id=_LIST, control_type="List"))
        return [info.name for info in engine.find_all(item_list, ElementSelector(control_type="ListItem"))]

    def _demonstrate_add(self, text):
        """L'utente scrive `text` nel campo e preme Aggiungi, con mouse e tastiera veri."""
        import pyautogui

        self._human_click(*self._center(ElementSelector(automation_id=_INPUT)))
        pyautogui.write(text, interval=0.02)
        time.sleep(0.3)
        self._human_click(*self._center(ElementSelector(name="Aggiungi", control_type="Button")))


class DemonstrationRecordingTests(_FixtureCase):
    def test_real_clicks_and_typing_become_semantic_steps(self):
        sampler = UiaDemonstrationSampler(_TITLE)
        self.assertTrue(sampler.start(), sampler.error)
        self._demonstrate_add("dimostrato")
        steps = sampler.stop()

        self.assertEqual([s.action for s in steps], [ACTION_TYPE, ACTION_CLICK])
        self.assertEqual(steps[0].selector.automation_id, _INPUT)
        self.assertEqual(steps[0].text, "dimostrato")
        self.assertIn(steps[1].selector.control_type, ("Button",))
        for step in steps:
            self.assertEqual(step.selector.window_title_contains, _TITLE)
        self.assertIn("dimostrato", self._list_items(), "la dimostrazione ha davvero agito sull'app")


class ProcedureSurvivesRestartResizeAndDataTests(_FixtureCase):
    def test_a_demonstrated_procedure_runs_after_restart_resize_and_with_new_data(self):
        recorder = RecordComputerProcedureSkill(self.manager)
        started = recorder.execute({"action": "start", "window": _TITLE})
        self.assertTrue(started.success, started)
        self._demonstrate_add("primo dato")
        saved = recorder.execute({"action": "stop", "name": "aggiungi voce"})
        self.assertTrue(saved.success, saved)
        self.assertEqual(saved.data["version"], 1)
        self.assertEqual(saved.data["app"].lower(), "python.exe")
        self.assertIsNotNone(saved.data["app_version"])
        (parameter,) = saved.data["parameters"]
        self.assertGreater(len(self.manager.load_procedure("aggiungi voce").structure), 5, "scheletro della finestra salvato")
        self.assertTrue(any("predefinito: 'primo dato'" in line for line in saved.data["steps"]), saved.data["steps"])

        # riavvio dell'app, finestra ridimensionata e spostata, dati diversi
        for process in list(self._processes):
            self._kill(process)
        self._processes.clear()
        self.window = self._launch()
        executor = ActionExecutor()
        executor.resize_window(self.window, 520, 900)
        executor.move_window(self.window, 60, 40)

        runner = RunComputerProcedureSkill(self.manager)
        result = runner.execute({"name": "aggiungi voce", "parameters": {parameter: "dato diverso"}})
        self.assertTrue(result.success, result)
        self.assertIn("dato diverso", self._list_items())
        self.assertNotIn("primo dato", self._list_items())


class DriftSuspensionTests(_FixtureCase):
    def test_a_drifted_procedure_is_suspended_and_never_improvised(self):
        self.manager.create("fantasma", [
            RecordedStep(action=ACTION_TYPE, selector=ElementSelector(automation_id=_INPUT, window_title_contains=_TITLE),
                         text="non deve comparire"),
            RecordedStep(action=ACTION_CLICK, selector=ElementSelector(name="Bottone sparito", window_title_contains=_TITLE)),
        ], app_process="python.exe")
        runner = RunComputerProcedureSkill(self.manager)
        result = runner.execute({"name": "fantasma"})
        self.assertFalse(result.success)
        self.assertTrue(result.data["suspended"])
        self.assertEqual(self.manager.load_procedure("fantasma").status, STATUS_SUSPENDED)

        again = runner.execute({"name": "fantasma"})
        self.assertEqual(again.error, "PROCEDURE_SUSPENDED")
        still = runner.execute({"name": "fantasma", "reactivate": True})
        self.assertFalse(still.success)
        self.assertEqual(self.manager.load_procedure("fantasma").status, STATUS_SUSPENDED)
        self.assertNotIn("non deve comparire", self._list_items())

    def test_a_different_app_than_the_demonstrated_one_suspends_without_any_input(self):
        self.manager.create("su notepad", [
            RecordedStep(action=ACTION_TYPE, selector=ElementSelector(automation_id=_INPUT, window_title_contains=_TITLE),
                         text="mai"),
        ], app_process="notepad.exe")
        result = RunComputerProcedureSkill(self.manager).execute({"name": "su notepad"})
        self.assertFalse(result.success)
        self.assertIn("python.exe", result.data["drift"])
        self.assertEqual(result.data["completed_steps"], 0)
        self.assertEqual(self.adapter.read_value(SelectorEngine(self.adapter).find_unique_element(
            self.window, ElementSelector(automation_id=_INPUT))), "")


class UndoAndReapprovalTests(_FixtureCase):
    def _input_value(self):
        engine = SelectorEngine(self.adapter)
        return self.adapter.read_value(engine.find_unique_element(self.window, ElementSelector(automation_id=_INPUT)))

    def test_the_last_run_is_undone_back_to_the_previous_value(self):
        self.manager.create("scrivi", [
            RecordedStep(action=ACTION_TYPE, selector=ElementSelector(automation_id=_INPUT, window_title_contains=_TITLE),
                         text="da annullare"),
        ], app_process="python.exe")
        runner = RunComputerProcedureSkill(self.manager)
        result = runner.execute({"name": "scrivi"})
        self.assertTrue(result.success, result)
        self.assertEqual(self._input_value(), "da annullare")
        self.assertEqual(result.data["undo_steps"], 1)

        undone = runner.execute({"name": "scrivi", "undo": True})
        self.assertTrue(undone.success, undone)
        self.assertEqual(self._input_value(), "")
        self.assertEqual(runner.execute({"name": "scrivi", "undo": True}).error, "NOTHING_TO_UNDO")

    def test_a_riskier_new_version_asks_approval_before_touching_the_app(self):
        step = RecordedStep(action=ACTION_TYPE, selector=ElementSelector(automation_id=_INPUT, window_title_contains=_TITLE),
                            text="modulo")
        self.manager.create("invio", [step], app_process="python.exe")
        riskier = RecordedStep(action=ACTION_TYPE, selector=step.selector, text="modulo inviato", risk_intent="UI_SUBMIT")
        self.manager.create("invio", [riskier], app_process="python.exe")

        runner = RunComputerProcedureSkill(self.manager)
        asked = runner.execute({"name": "invio"})
        self.assertEqual(asked.error, "CONFIRMATION_REQUIRED")
        self.assertIn("UI_SUBMIT", asked.data["message"])
        self.assertEqual(self._input_value(), "", "nessun input prima della nuova approvazione")

        from core.policy_engine import PolicyEngine

        runner.policy_engine = PolicyEngine()
        done = runner.execute(asked.data["confirm_parameters"])
        self.assertTrue(done.success, done)
        self.assertEqual(self._input_value(), "modulo inviato")
        self.assertEqual(runner.execute({"name": "invio"}).error, None, "riapprovata: niente seconda domanda")


if __name__ == "__main__":
    unittest.main()
