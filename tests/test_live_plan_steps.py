"""F4.5.2 ("piano e passi live con stato e durata"): un agente vero (modello scriptato, registro finto) collegato
alle callback di JakeCore pubblica ogni passo prima come "in corso" e poi con esito e durata; il riduttore
dell'HUD (lo stesso contratto del C++) ne ricava il piano del compito."""
import json
import tempfile
import unittest
from pathlib import Path

from core.hud_view_state import HudViewState
from core.session_hooks import SessionHooks
from core.skill_result import SkillResult
from tests.test_agent import FakeRegistry, ScriptedOllamaClient, _agent
from tests.test_jake_core_pipeline import _bare_core


class LivePlanStepsTests(unittest.TestCase):
    def test_an_agent_run_becomes_a_live_plan_with_outcomes_and_durations(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl")
        core.session_hooks = SessionHooks()
        events = core.event_bus.subscribe()
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={}),
                                                  SkillResult(success=False, data={}, error="INVALID_PARAMETERS")])
        client = ScriptedOllamaClient([
            {"thought": "Salvo il primo", "action": {"intent": "ADD_NOTE", "parameters": {"text": "uno"}}, "final_answer": "", "ask_user": ""},
            {"thought": "Salvo il secondo", "action": {"intent": "ADD_NOTE", "parameters": {"text": "due"}}, "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto in parte.", "ask_user": ""},
        ])
        agent = _agent(registry, client)
        agent.on_step, agent.on_step_completed = core._on_agent_step, core._on_agent_step_completed
        agent.run("salva due appunti")

        view = HudViewState()
        view.connection_started()
        while not events.empty():
            view.apply(json.loads(events.get_nowait().to_json()))
        self.assertEqual([(s["step"], s["description"], s["status"]) for s in view.plan_steps],
                         [(1, "Salvo il primo", "done"), (2, "Salvo il secondo", "failed")])
        self.assertTrue(all(isinstance(s["duration_ms"], int) and s["duration_ms"] >= 0 for s in view.plan_steps))


if __name__ == "__main__":
    unittest.main()
