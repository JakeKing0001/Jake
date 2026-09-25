"""F3.8.1-F3.8.7 senza UI: dalla dimostrazione ai passi, generalizzazione in parametri, descrizione
all'utente, procedura versionata con app/approvazione/stato, ri-approvazione su aumento di rischio o
capability, formato storico ancora leggibile. I test sull'app vera sono in
tests/test_procedure_demonstration.py."""
import tempfile
import unittest
from pathlib import Path

from core.computer_use.demonstration import DemonstrationRecorder, Observation, describe_steps, generalize
from core.computer_use.procedure import ACTION_CLICK, ACTION_TYPE, RecordedStep
from core.computer_use.procedure_lifecycle import (
    STATUS_SUSPENDED,
    Procedure,
    approve,
    assess,
    needs_reapproval,
    reactivate,
    suspend,
)
from core.computer_use.selector import ElementSelector
from core.memory_manager import MemoryManager
from core.procedure_manager import ProcedureManager
from core.risk import RiskLevel

INPUT = {"name": "Campo", "control_type": "Edit", "automation_id": "QApplication.w.fixture_input"}
ADD = {"name": "Aggiungi", "control_type": "Button", "automation_id": ""}
PASSWORD = {"name": "Password", "control_type": "Edit", "automation_id": "QApplication.w.password_field"}


def _step(action, name, text=None, risk_intent=None, undo=None):
    return RecordedStep(action=action, selector=ElementSelector(name=name, window_title_contains="Fixture"),
                        text=text, risk_intent=risk_intent, undo=undo)


class DemonstrationRecorderTests(unittest.TestCase):
    def test_a_focus_click_followed_by_typing_becomes_one_type_step_then_the_click(self):
        rec = DemonstrationRecorder("Fixture")
        rec.observe(Observation("click", **INPUT))
        rec.observe(Observation("value", **INPUT, value="ciao"))
        rec.observe(Observation("click", **ADD))
        steps = rec.steps()
        self.assertEqual([s.action for s in steps], [ACTION_TYPE, ACTION_CLICK])
        self.assertEqual(steps[0].text, "ciao")
        self.assertEqual(steps[0].selector.automation_id, "QApplication.w.fixture_input")
        self.assertEqual(steps[1].selector.name, "Aggiungi")
        for step in steps:
            self.assertEqual(step.selector.window_title_contains, "Fixture")

    def test_the_last_value_of_a_field_edited_twice_wins(self):
        rec = DemonstrationRecorder("Fixture")
        rec.observe(Observation("value", **INPUT, value="prima"))
        rec.observe(Observation("value", **INPUT, value="dopo"))
        self.assertEqual([s.text for s in rec.steps()], ["dopo"])

    def test_a_password_is_never_recorded_only_a_required_parameter(self):
        rec = DemonstrationRecorder("Fixture")
        rec.observe(Observation("value", **PASSWORD, secret=True))
        (step,) = rec.steps()
        self.assertEqual(step.text, "${password}")
        generalized, defaults = generalize([step])
        self.assertEqual(generalized[0].text, "${password}")
        self.assertNotIn("password", defaults)

    def test_an_element_without_semantic_identity_is_never_turned_into_coordinates(self):
        rec = DemonstrationRecorder("Fixture")
        rec.observe(Observation("click", name="", control_type="Pane", automation_id=""))
        self.assertEqual(rec.steps(), [])

    def test_a_target_window_is_required(self):
        with self.assertRaises(ValueError):
            DemonstrationRecorder("  ")


class GeneralizeAndDescribeTests(unittest.TestCase):
    def test_typed_literals_become_parameters_with_the_demonstrated_default(self):
        steps = [_step(ACTION_TYPE, "Nome", text="Mario"), _step(ACTION_TYPE, "Nome", text="Rossi"), _step(ACTION_CLICK, "Invia")]
        generalized, defaults = generalize(steps)
        self.assertEqual([s.text for s in generalized], ["${nome}", "${nome_2}", None])
        self.assertEqual(defaults, {"nome": "Mario", "nome_2": "Rossi"})

    def test_the_description_shows_parameters_defaults_and_declared_risk(self):
        steps, defaults = generalize([_step(ACTION_TYPE, "Nome", text="Mario"),
                                      _step(ACTION_CLICK, "Elimina", risk_intent="UI_DELETE")])
        lines = describe_steps(steps, defaults)
        self.assertIn("nome (predefinito: 'Mario')", lines[0])
        self.assertIn("Clicca «Elimina»", lines[1])
        self.assertIn("UI_DELETE", lines[1])


class ProcedureModelTests(unittest.TestCase):
    def test_round_trip_keeps_version_app_defaults_approval_status_and_undo(self):
        undo = _step(ACTION_CLICK, "Rimuovi")
        procedure = approve(Procedure(name="p", steps=(_step(ACTION_CLICK, "Aggiungi", undo=undo),), version=0,
                                      app_process="python.exe", app_version="3.12.6.0", defaults=(("x", "1"),)))
        procedure = suspend(procedure, "passo 1: NOT_FOUND")
        restored = Procedure.from_dict(procedure.to_dict())
        self.assertEqual(restored, procedure)
        self.assertEqual(restored.steps[0].undo, undo)
        self.assertEqual(restored.status, STATUS_SUSPENDED)
        self.assertEqual(reactivate(restored).status, "active")

    def test_an_unknown_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            Procedure.from_dict({"schema_version": 99, "name": "p", "steps": []})

    def test_risk_includes_undo_steps_and_the_app_is_a_capability(self):
        level, caps = assess([_step(ACTION_CLICK, "Aggiungi", undo=_step(ACTION_CLICK, "Elimina", risk_intent="UI_DELETE"))],
                             "Notepad.exe")
        self.assertEqual(level, RiskLevel.DESTRUCTIVE)
        self.assertEqual(caps, ("app:notepad.exe", "risk:UI_DELETE"))

    def test_more_risk_or_a_new_capability_needs_reapproval_less_does_not(self):
        base = approve(Procedure(name="p", steps=(_step(ACTION_CLICK, "Aggiungi"),), app_process="python.exe", version=0))
        self.assertEqual(needs_reapproval(base), [])
        riskier = Procedure(**{**base.__dict__, "steps": (_step(ACTION_CLICK, "Elimina", risk_intent="UI_DELETE"),)})
        reasons = needs_reapproval(riskier)
        self.assertTrue(any("rischio salito" in r for r in reasons))
        self.assertTrue(any("risk:UI_DELETE" in r for r in reasons))
        other_app = Procedure(**{**base.__dict__, "app_process": "notepad.exe"})
        self.assertTrue(any("app:notepad.exe" in r for r in needs_reapproval(other_app)))
        approved = approve(riskier)
        self.assertEqual(needs_reapproval(approved), [])
        self.assertEqual(approved.version, base.version + 1)


class ProcedureManagerLifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = MemoryManager(db_path=Path(tmp.name) / "memory.db")
        self.addCleanup(memory.close)  # prima della cartella: su Windows il file aperto non si cancella
        self.manager = ProcedureManager(memory)

    def test_create_approves_the_first_version_and_keeps_approval_on_overwrite(self):
        first = self.manager.create("p", [_step(ACTION_CLICK, "Aggiungi")], app_process="python.exe")
        self.assertEqual(first.version, 1)
        self.assertEqual(needs_reapproval(first), [])
        second = self.manager.create("p", [_step(ACTION_CLICK, "Elimina", risk_intent="UI_DELETE")], app_process="python.exe")
        self.assertEqual(second.version, 2)
        self.assertTrue(needs_reapproval(self.manager.load_procedure("p")), "passi piu' rischiosi: serve riapprovare")

    def test_a_legacy_list_is_read_as_an_approved_version_one(self):
        self.manager.save("vecchia", [_step(ACTION_CLICK, "Elimina", risk_intent="DELETE_PATH")])
        procedure = self.manager.load_procedure("vecchia")
        self.assertEqual(procedure.version, 1)
        self.assertEqual(needs_reapproval(procedure), [])
        self.assertEqual(len(self.manager.load("vecchia")), 1)


if __name__ == "__main__":
    unittest.main()


class StructuralDriftTests(unittest.TestCase):
    """F3.3.5 adottato: la struttura della finestra dimostrata e' confrontata PRIMA di agire."""

    def _procedure(self, structure):
        return Procedure(name="p", steps=(_step(ACTION_CLICK, "Aggiungi"),), structure=tuple(structure))

    def test_a_very_different_window_suspends_before_any_step(self):
        from unittest import mock

        from core.computer_use.procedure_lifecycle import run_procedure
        from core.computer_use.ui_automation_adapter import ElementInfo

        def info(name, control_type, children=()):
            return ElementInfo(name, "", control_type, (0, 0, 1, 1), True, None, None, False, tuple(children))

        demonstrated = info("F", "Window", [info("Aggiungi", "Button"), info("Rimuovi", "Button"), info("Lista", "List")])
        different = info("F", "Window", [info("Stampa", "Button"), info("Esporta", "MenuItem")])
        from core.computer_use.selector_store import structure_tokens

        adapter = mock.MagicMock()
        adapter.describe_tree.return_value = different
        agent = mock.MagicMock()
        run = run_procedure(agent, adapter, self._procedure(sorted(structure_tokens(demonstrated))))
        self.assertIn("struttura", run.drift)
        agent.click_element.assert_not_called()
        agent.type_into_element.assert_not_called()

        adapter.describe_tree.return_value = demonstrated
        with mock.patch("core.computer_use.procedure_lifecycle.replay_step") as replay:
            replay.return_value = mock.MagicMock(success=True)
            run = run_procedure(agent, adapter, self._procedure(sorted(structure_tokens(demonstrated))))
        self.assertIsNone(run.drift)
        replay.assert_called_once()

    def test_the_structure_survives_the_round_trip(self):
        procedure = self._procedure(("Button:Aggiungi", "Window"))
        self.assertEqual(Procedure.from_dict(procedure.to_dict()).structure, ("Button:Aggiungi", "Window"))


class RecordSkillTargetTests(unittest.TestCase):
    def test_without_a_window_the_foreground_one_is_recorded_by_process(self):
        from unittest import mock

        from skills.computer_procedure import RecordComputerProcedureSkill

        with mock.patch("core.computer_use.demonstration.UiaDemonstrationSampler") as Sampler:
            Sampler.return_value.start.return_value = True
            skill = RecordComputerProcedureSkill(procedure_manager=None, foreground_title=lambda: ("Calcolatrice", 4321))
            result = skill.execute({"action": "start"})
        self.assertTrue(result.success)
        Sampler.assert_called_once_with("Calcolatrice", process_id=4321)
        self.assertEqual(skill.execute({"action": "start"}).error, "ALREADY_RECORDING")

    def test_stop_without_recording_or_name_is_reported(self):
        from skills.computer_procedure import RecordComputerProcedureSkill

        self.assertEqual(RecordComputerProcedureSkill(None).execute({"action": "stop", "name": "x"}).error, "NOT_RECORDING")
        self.assertEqual(RecordComputerProcedureSkill(None).execute({"action": "boh"}).error, "MISSING_PARAMETERS")

