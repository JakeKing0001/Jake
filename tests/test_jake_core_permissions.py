"""Test unitari per il gate di conferma centralizzato (v3.2) dentro JakeCore._resolve_and_execute.

Costruisce un JakeCore "spoglio" con JakeCore.__new__ invece di passare da __init__ (che
istanzierebbe l'intero registro di skill, Ollama, NEST...): _resolve_and_execute usa solo
self.skill_registry e self.always_confirm_intents, quindi bastano quelli per testare la logica
del gate senza toccare nulla di esterno (stesso approccio delle fake minimali in
tests/test_agentics.py)."""
import unittest

from core.command import Command
from core.jake_core import JakeCore
from core.skill_result import SkillResult


class FakeSkill:
    metadata = {"intent": "FAKE", "description": "Una skill finta.", "parameters": {}}

    def __init__(self, result=None):
        self.result = result if result is not None else SkillResult(success=True, data={})
        self.calls = []

    def execute(self, parameters=None):
        self.calls.append(parameters)
        return self.result


class FakeRegistry:
    def __init__(self, skills: dict):
        self._skills = skills

    def get_skill(self, intent):
        return self._skills.get(intent)

    def has_skill(self, intent):
        return intent in self._skills

    def execute(self, intent, parameters=None):
        skill = self.get_skill(intent)
        return None if skill is None else skill.execute(parameters)


def _bare_core(skill_registry, always_confirm_intents) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.skill_registry = skill_registry
    core.always_confirm_intents = set(always_confirm_intents)
    return core


class ResolveAndExecuteConfirmationGateTests(unittest.TestCase):
    def test_intent_requiring_confirmation_is_not_executed_yet(self):
        skill = FakeSkill()
        registry = FakeRegistry({"FORGET": skill})
        core = _bare_core(registry, always_confirm_intents={"FORGET"})

        resolved, result, note = core._resolve_and_execute(Command("FORGET", {"topic": "tutto"}))

        self.assertEqual(resolved.intent, "FORGET")
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(skill.calls, [], "la skill non deve essere eseguita finche' non e' confermata")
        self.assertIsNone(note)

    def test_confirmation_message_and_confirm_parameters_are_populated(self):
        skill = FakeSkill()
        registry = FakeRegistry({"FORGET": skill})
        core = _bare_core(registry, always_confirm_intents={"FORGET"})

        _, result, _ = core._resolve_and_execute(Command("FORGET", {"topic": "tutto"}))

        self.assertIn("Confermi", result.data["message"])
        self.assertEqual(result.data["confirm_intent"], "FORGET")
        self.assertEqual(result.data["confirm_parameters"], {"topic": "tutto", "confirmed": True})

    def test_confirmed_parameter_bypasses_the_gate_and_executes_for_real(self):
        skill = FakeSkill(SkillResult(success=True, data={"done": True}))
        registry = FakeRegistry({"FORGET": skill})
        core = _bare_core(registry, always_confirm_intents={"FORGET"})

        resolved, result, _ = core._resolve_and_execute(Command("FORGET", {"topic": "tutto", "confirmed": True}))

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)
        self.assertEqual(resolved.intent, "FORGET")

    def test_intent_not_in_always_confirm_executes_directly(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"GET_TIME": skill})
        core = _bare_core(registry, always_confirm_intents={"FORGET"})

        _, result, _ = core._resolve_and_execute(Command("GET_TIME", {}))

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)

    def test_empty_always_confirm_intents_never_gates_anything(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"DELETE_TODO": skill})
        core = _bare_core(registry, always_confirm_intents=set())

        _, result, _ = core._resolve_and_execute(Command("DELETE_TODO", {"id": 1}))

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)


class SharedGateCoversAgentAndDirectPathsTests(unittest.TestCase):
    """La ragione stessa del refactor: prima il gate viveva solo in JakeCore._execute_command,
    quindi l'agente a passi (che chiama _resolve_and_execute direttamente, non _execute_command)
    poteva eseguire un'azione DESTRUCTIVE/ADMIN senza mai chiedere conferma. Verifica che lo
    stesso identico metodo usato dall'executor dell'agente (vedi core/agent.py, TaskAgent.executor)
    applichi il gate."""

    def test_agent_style_direct_call_to_resolve_and_execute_is_gated(self):
        skill = FakeSkill()
        registry = FakeRegistry({"RESTART_EXPLORER": skill})
        core = _bare_core(registry, always_confirm_intents={"RESTART_EXPLORER"})

        # Stessa identica chiamata che TaskAgent.executor farebbe per un passo dell'agente.
        executor = lambda intent, parameters: core._resolve_and_execute(Command(intent, parameters))[1]
        result = executor("RESTART_EXPLORER", {})

        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(skill.calls, [])


if __name__ == "__main__":
    unittest.main()
