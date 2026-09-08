"""Test unitari per il gate di conferma/autenticazione centralizzato (v3.2 + v5.4/5.5) dentro
JakeCore._resolve_and_execute e JakeCore._handle_confirmation.

Costruisce un JakeCore "spoglio" con JakeCore.__new__ invece di passare da __init__ (che
istanzierebbe l'intero registro di skill, Ollama, NEST...): i metodi testati qui usano solo
pochi attributi (skill_registry, always_confirm_intents, auth_gate, require_auth_intents,
conversation_state, learning, last_route), quindi bastano quelli per testare la logica del gate
senza toccare nulla di esterno (stesso approccio delle fake minimali in tests/test_agentics.py)."""
import unittest

from core.auth_gate import AuthGate
from core.command import Command
from core.conversation_state import ConversationStateManager
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


def _bare_core(skill_registry, always_confirm_intents, auth_gate=None, require_auth_intents=None) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.skill_registry = skill_registry
    core.always_confirm_intents = set(always_confirm_intents)
    core.auth_gate = auth_gate or AuthGate()  # disabilitata per default: nessuna passphrase
    core.require_auth_intents = set(require_auth_intents or set())
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


class RequireAuthGateTests(unittest.TestCase):
    """v5.4/5.5: il gradino REQUIRE_AUTH. Opt-in - senza passphrase configurata, un intent in
    require_auth_intents non viene mai bloccato qui (ricade sul CONFIRM ordinario, se presente
    anche in always_confirm_intents, come fa gia' oggi il censimento in core/risk.py)."""

    def test_admin_intent_is_gated_when_auth_is_enabled(self):
        skill = FakeSkill()
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(
            registry, always_confirm_intents=set(), auth_gate=AuthGate(passphrase="apri sesamo"),
            require_auth_intents={"SET_POWER_PLAN"},
        )

        resolved, result, note = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))

        self.assertEqual(result.error, "AUTH_REQUIRED")
        self.assertEqual(skill.calls, [], "la skill non deve eseguire finche' non e' autenticata")
        self.assertEqual(result.data["confirm_intent"], "SET_POWER_PLAN")
        self.assertEqual(result.data["confirm_parameters"], {"plan": "balanced", "authenticated": True})

    def test_admin_intent_is_not_gated_by_auth_when_disabled(self):
        """Auth non configurata: nessun blocco qui, il flusso normale (CONFIRM, se applicabile)
        resta l'unica protezione, esattamente come prima della v5.4/5.5."""
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(registry, always_confirm_intents=set(), require_auth_intents={"SET_POWER_PLAN"})

        _, result, _ = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)

    def test_authenticated_parameter_bypasses_the_gate_and_executes_for_real(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(
            registry, always_confirm_intents=set(), auth_gate=AuthGate(passphrase="apri sesamo"),
            require_auth_intents={"SET_POWER_PLAN"},
        )

        _, result, _ = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced", "authenticated": True}))

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)

    def test_auth_gate_is_checked_before_the_confirm_gate(self):
        """Se lo stesso intent e' sia in require_auth_intents sia in always_confirm_intents
        (il caso normale per ADMIN, vedi core/risk.py), con auth attiva vince AUTH_REQUIRED."""
        skill = FakeSkill()
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(
            registry, always_confirm_intents={"SET_POWER_PLAN"}, auth_gate=AuthGate(passphrase="apri sesamo"),
            require_auth_intents={"SET_POWER_PLAN"},
        )

        _, result, _ = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))

        self.assertEqual(result.error, "AUTH_REQUIRED")


class FakeLearning:
    def observe(self, *args, **kwargs):
        pass


def _bare_core_for_confirmation(skill_registry, auth_gate) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.skill_registry = skill_registry
    core.auth_gate = auth_gate
    core.conversation_state = ConversationStateManager()
    core.learning = FakeLearning()
    core.last_route = None
    core.last_exchange = None
    return core


class HandleConfirmationAuthTests(unittest.TestCase):
    """v5.4/5.5: JakeCore._handle_confirmation, il lato che riceve la risposta dell'utente a
    un'azione AUTH_REQUIRED (impostata da _resolve_and_execute, vedi RequireAuthGateTests)."""

    def _pending_auth_action(self, core, intent="SET_POWER_PLAN", parameters=None):
        core.conversation_state.set_pending_action({
            "intent": intent, "parameters": parameters or {"plan": "balanced", "authenticated": True},
            "reason": "auth_required", "text": "metti il pc in risparmio energetico",
        })

    def test_correct_passphrase_executes_the_action(self):
        skill = FakeSkill(SkillResult(success=True, data={"plan": "balanced"}))
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core_for_confirmation(registry, AuthGate(passphrase="apri sesamo"))
        self._pending_auth_action(core)

        response = core._handle_confirmation("apri sesamo")

        self.assertEqual(len(skill.calls), 1)
        self.assertFalse(core.conversation_state.has_pending_action())
        self.assertTrue(response)

    def test_wrong_passphrase_cancels_without_executing(self):
        skill = FakeSkill()
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core_for_confirmation(registry, AuthGate(passphrase="apri sesamo"))
        self._pending_auth_action(core)

        response = core._handle_confirmation("password sbagliata")

        self.assertEqual(skill.calls, [])
        self.assertIn("errata", response.lower())
        self.assertFalse(core.conversation_state.has_pending_action())

if __name__ == "__main__":
    unittest.main()
