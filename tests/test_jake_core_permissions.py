"""Test unitari per il gate di conferma/autenticazione centralizzato (v3.2 + v5.4/5.5) dentro
JakeCore._resolve_and_execute e JakeCore._handle_confirmation.

Costruisce un JakeCore "spoglio" con JakeCore.__new__ invece di passare da __init__ (che
istanzierebbe l'intero registro di skill, Ollama, NEST...): i metodi testati qui usano solo
pochi attributi (skill_registry, policy_engine, auth_gate, conversation_state, learning,
last_route), quindi bastano quelli per testare la logica del gate senza toccare nulla di
esterno (stesso approccio delle fake minimali in tests/test_agentics.py). policy_engine e'
un core.policy_engine.PolicyEngine vero (F1: la separazione formale planner/policy/executor
completata in questa sessione), non piu' tre insiemi (blocked_intents/always_confirm_intents/
require_auth_intents) impostati direttamente su core."""
import unittest
import tempfile
from pathlib import Path
from unittest import mock

from core.action_ledger import ActionLedger
from core.auth_gate import AuthGate
from core.command import Command
from core.conversation_state import ConversationStateManager
from core.jake_core import JakeCore
from core.policy_engine import PolicyEngine
from core.session_recorder import SessionRecorder
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

    def execute(self, intent, parameters=None, policy_engine=None):
        skill = self.get_skill(intent)
        return None if skill is None else skill.execute(parameters)


def _bare_core(
    skill_registry, always_confirm_intents, auth_gate=None, require_auth_intents=None, blocked_intents=None,
) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.skill_registry = skill_registry
    core.auth_gate = auth_gate or AuthGate()  # disabilitata per default: nessuna passphrase
    core.policy_engine = PolicyEngine(
        auth_gate=core.auth_gate, blocked_intents=blocked_intents,
        always_confirm_intents=always_confirm_intents, require_auth_intents=require_auth_intents,
    )
    return core


class ResolveAndExecuteConfirmationGateTests(unittest.TestCase):
    def test_intent_requiring_confirmation_is_not_executed_yet(self):
        skill = FakeSkill()
        registry = FakeRegistry({"FORGET": skill})
        core = _bare_core(registry, always_confirm_intents={"FORGET"})

        execution = core._resolve_and_execute(Command("FORGET", {"topic": "tutto"}))
        resolved, result, note = execution.command, execution.result, execution.note

        self.assertEqual(resolved.intent, "FORGET")
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(skill.calls, [], "la skill non deve essere eseguita finche' non e' confermata")
        self.assertIsNone(note)

    def test_confirmation_message_and_confirm_parameters_are_populated(self):
        skill = FakeSkill()
        registry = FakeRegistry({"FORGET": skill})
        core = _bare_core(registry, always_confirm_intents={"FORGET"})

        execution = core._resolve_and_execute(Command("FORGET", {"topic": "tutto"}))
        result = execution.result

        self.assertIn("Confermi", result.data["message"])
        self.assertEqual(result.data["confirm_intent"], "FORGET")
        self.assertEqual(result.data["confirm_parameters"], {"topic": "tutto", "confirmed": True})

    def test_confirmed_parameter_bypasses_the_gate_and_executes_for_real(self):
        skill = FakeSkill(SkillResult(success=True, data={"done": True}))
        registry = FakeRegistry({"FORGET": skill})
        core = _bare_core(registry, always_confirm_intents={"FORGET"})

        execution = core._resolve_and_execute(Command("FORGET", {"topic": "tutto", "confirmed": True}))
        resolved, result = execution.command, execution.result

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)
        self.assertEqual(resolved.intent, "FORGET")

    def test_intent_not_in_always_confirm_executes_directly(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"GET_TIME": skill})
        core = _bare_core(registry, always_confirm_intents={"FORGET"})

        execution = core._resolve_and_execute(Command("GET_TIME", {}))
        result = execution.result

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)

    def test_empty_always_confirm_intents_never_gates_anything(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"DELETE_TODO": skill})
        core = _bare_core(registry, always_confirm_intents=set())

        execution = core._resolve_and_execute(Command("DELETE_TODO", {"id": 1}))
        result = execution.result

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
        executor = lambda intent, parameters: core._resolve_and_execute(Command(intent, parameters)).result
        result = executor("RESTART_EXPLORER", {})

        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(skill.calls, [])


class FallbackAlternativeGateTests(unittest.TestCase):
    """F1 (difesa in profondita', non un buco gia' sfruttabile: vedi ROADMAP.md): core/
    fallbacks.py::alternative_for restituisce oggi solo alternative fisse a basso rischio
    (OPEN_URL/OPEN_APP/CLICK_ELEMENT/CLOSE_WINDOW), ma _resolve_and_execute la eseguiva
    saltando decide_interactive() del tutto - un punto cieco strutturale se una futura
    alternativa mappasse verso un intent DESTRUCTIVE/ADMIN. Qui si forza alternative_for (con
    un mock) a restituire un intent rischioso, per verificare che la policy lo fermi comunque,
    indipendentemente da cosa la funzione vera restituisce oggi."""

    def test_risky_alternative_is_not_executed_without_confirmation(self):
        import unittest.mock

        original_skill = FakeSkill(SkillResult(success=False, data={}, error="NOT_FOUND"))
        risky_alt_skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"OPEN_APP": original_skill, "RISKY_ALT": risky_alt_skill})
        core = _bare_core(registry, always_confirm_intents={"RISKY_ALT"})

        with unittest.mock.patch(
            "core.jake_core.fallbacks.alternative_for",
            return_value=(Command("RISKY_ALT", {}), None),
        ):
            execution = core._resolve_and_execute(Command("OPEN_APP", {"app": "x"}))
            result = execution.result

        self.assertEqual(risky_alt_skill.calls, [], "l'alternativa rischiosa non deve eseguire senza conferma")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND", "ripiega sul fallimento originale, non su una nuova richiesta di conferma")

    def test_safe_alternative_still_executes_as_before(self):
        """Verifica che l'indurimento non rompa il caso normale: un'alternativa che la policy
        lascia passare (ALLOW) deve continuare a eseguire, come sempre."""
        import unittest.mock

        original_skill = FakeSkill(SkillResult(success=False, data={}, error="NOT_FOUND"))
        safe_alt_skill = FakeSkill(SkillResult(success=True, data={"opened": True}))
        registry = FakeRegistry({"OPEN_APP": original_skill, "OPEN_URL": safe_alt_skill})
        core = _bare_core(registry, always_confirm_intents=set())

        with unittest.mock.patch(
            "core.jake_core.fallbacks.alternative_for",
            return_value=(Command("OPEN_URL", {"url": "https://example.com"}), None),
        ):
            execution = core._resolve_and_execute(Command("OPEN_APP", {"app": "example.com"}))
            resolved, result = execution.command, execution.result

        self.assertEqual(len(safe_alt_skill.calls), 1)
        self.assertTrue(result.success)
        self.assertEqual(resolved.intent, "OPEN_URL")


class BlockedIntentsGateTests(unittest.TestCase):
    """F1 (core/policy_engine.py): buco reale trovato e corretto, non un'ipotesi. Prima di questa
    correzione blocked_intents veniva controllato SOLO in JakeCore._execute_command, mai dentro
    _resolve_and_execute - esattamente come il gate di conferma prima del refactor sopra
    (SharedGateCoversAgentAndDirectPathsTests), ma per blocked_intents nessuno lo aveva ancora
    spostato: un intent che l'utente aveva esplicitamente disabilitato in config.json restava
    comunque eseguibile dall'agente a passi (generale, coding, ricerca), che passa da
    _resolve_and_execute e non da _execute_command. Riprodotto per davvero con un JakeCore reale
    prima di correggere: la skill veniva eseguita nonostante blocked_intents la contenesse."""

    def test_blocked_intent_is_never_executed_via_the_agent_style_direct_call(self):
        skill = FakeSkill()
        registry = FakeRegistry({"CLEAR_TEMP_FILES": skill})
        core = _bare_core(registry, always_confirm_intents=set(), blocked_intents={"CLEAR_TEMP_FILES"})

        executor = lambda intent, parameters: core._resolve_and_execute(Command(intent, parameters)).result
        result = executor("CLEAR_TEMP_FILES", {})

        self.assertEqual(result.error, "POLICY_BLOCKED")
        self.assertEqual(skill.calls, [])

    def test_blocked_wins_over_an_already_confirmed_parameter(self):
        """Un blocco di policy non e' aggirabile nemmeno se i parametri arrivano gia' con
        confirmed=True (es. un secondo passo di un'azione gia' avviata)."""
        skill = FakeSkill()
        registry = FakeRegistry({"CLEAR_TEMP_FILES": skill})
        core = _bare_core(registry, always_confirm_intents=set(), blocked_intents={"CLEAR_TEMP_FILES"})

        execution = core._resolve_and_execute(Command("CLEAR_TEMP_FILES", {"confirmed": True}))
        result = execution.result

        self.assertEqual(result.error, "POLICY_BLOCKED")
        self.assertEqual(skill.calls, [])

    def test_intent_not_in_blocked_intents_is_unaffected(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"GET_TIME": skill})
        core = _bare_core(registry, always_confirm_intents=set(), blocked_intents={"CLEAR_TEMP_FILES"})

        execution = core._resolve_and_execute(Command("GET_TIME", {}))
        result = execution.result

        self.assertTrue(result.success)
        self.assertEqual(len(skill.calls), 1)


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

        execution = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))
        result = execution.result

        self.assertEqual(result.error, "AUTH_REQUIRED")
        self.assertEqual(skill.calls, [], "la skill non deve eseguire finche' non e' autenticata")
        self.assertEqual(result.data["confirm_intent"], "SET_POWER_PLAN")
        self.assertEqual(
            result.data["confirm_parameters"],
            {"plan": "balanced", "authenticated": True, "authenticated_via": "passphrase"},
        )

    def test_admin_intent_is_not_gated_by_auth_when_disabled(self):
        """Auth non configurata: nessun blocco qui, il flusso normale (CONFIRM, se applicabile)
        resta l'unica protezione, esattamente come prima della v5.4/5.5."""
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(registry, always_confirm_intents=set(), require_auth_intents={"SET_POWER_PLAN"})

        execution = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))
        result = execution.result

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)

    def test_authenticated_parameter_bypasses_the_gate_and_executes_for_real(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(
            registry, always_confirm_intents=set(), auth_gate=AuthGate(passphrase="apri sesamo"),
            require_auth_intents={"SET_POWER_PLAN"},
        )

        execution = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced", "authenticated": True}))
        result = execution.result

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

        execution = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))
        result = execution.result

        self.assertEqual(result.error, "AUTH_REQUIRED")


class WindowsHelloAuthTests(unittest.TestCase):
    """F1: Windows Hello e' tentato PRIMA della passphrase quando entrambi sono attivi
    (JakeCore._resolve_and_execute) - windows_hello_verify e' SEMPRE iniettato in AuthGate qui,
    non chiama mai l'API vera (vedi core/windows_hello.py)."""

    def test_successful_windows_hello_executes_immediately_in_the_same_turn(self):
        """A differenza della passphrase (che ferma tutto con AUTH_REQUIRED e aspetta un turno
        in piu'), un Windows Hello riuscito non deve mai produrre AUTH_REQUIRED: esegue subito."""
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(
            registry, always_confirm_intents=set(),
            auth_gate=AuthGate(windows_hello_enabled=True, windows_hello_verify=lambda reason: True),
            require_auth_intents={"SET_POWER_PLAN"},
        )

        execution = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))
        resolved, result = execution.command, execution.result

        self.assertEqual(len(skill.calls), 1)
        self.assertTrue(result.success)
        self.assertEqual(resolved.parameters["authenticated_via"], "windows_hello")

    def test_failed_windows_hello_falls_back_to_the_passphrase_flow_unchanged(self):
        """Windows Hello annullato/non disponibile: deve ripiegare sul flusso passphrase
        esistente esattamente come se Windows Hello non fosse mai stato configurato."""
        skill = FakeSkill()
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(
            registry, always_confirm_intents=set(),
            auth_gate=AuthGate(
                passphrase="apri sesamo", windows_hello_enabled=True, windows_hello_verify=lambda reason: False,
            ),
            require_auth_intents={"SET_POWER_PLAN"},
        )

        execution = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))
        result = execution.result

        self.assertEqual(result.error, "AUTH_REQUIRED")
        self.assertEqual(skill.calls, [], "la skill non deve eseguire finche' non e' autenticata")
        self.assertEqual(result.data["confirm_parameters"]["authenticated_via"], "passphrase")

    def test_windows_hello_disabled_never_calls_the_injected_verifier(self):
        calls = []
        skill = FakeSkill()
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core(
            registry, always_confirm_intents=set(),
            auth_gate=AuthGate(
                passphrase="apri sesamo", windows_hello_enabled=False,
                windows_hello_verify=lambda reason: calls.append(reason) or True,
            ),
            require_auth_intents={"SET_POWER_PLAN"},
        )

        execution = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))
        result = execution.result

        self.assertEqual(calls, [])
        self.assertEqual(result.error, "AUTH_REQUIRED")

    def test_policy_revoked_while_windows_hello_is_open_prevents_execution(self):
        skill = FakeSkill()
        core = _bare_core(
            FakeRegistry({"SET_POWER_PLAN": skill}), always_confirm_intents=set(),
            auth_gate=AuthGate(windows_hello_enabled=True), require_auth_intents={"SET_POWER_PLAN"},
        )

        def verify_and_revoke(reason):
            core.policy_engine.blocked_intents.add("SET_POWER_PLAN")
            return True

        core.auth_gate._windows_hello_verify = verify_and_revoke
        execution = core._resolve_and_execute(Command("SET_POWER_PLAN", {"plan": "balanced"}))
        result = execution.result

        self.assertEqual(skill.calls, [])
        self.assertEqual(result.error, "POLICY_BLOCKED")


class FakeLearning:
    def observe(self, *args, **kwargs):
        pass


def _bare_core_for_confirmation(skill_registry, auth_gate) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.skill_registry = skill_registry
    core.auth_gate = auth_gate
    core.policy_engine = PolicyEngine(auth_gate=auth_gate)
    core.conversation_state = ConversationStateManager()
    core.learning = FakeLearning()
    core.last_route = None
    core.last_exchange = None
    # F1: _finalize_pending_action registra anche sul ledger/log strutturato (vedi
    # core/action_ledger.py) - servono anche qui, non solo private_mode/model, altrimenti
    # _log_action_outcome esplode su un attributo mai impostato da questo core "spoglio".
    core.private_mode = False
    core.model = "test-model"
    core.action_ledger = ActionLedger()
    core.session_recorder = SessionRecorder()
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

    def test_wrong_passphrase_writes_a_denied_receipt_to_the_ledger(self):
        """F1: fino a questa correzione, una passphrase sbagliata annullava l'azione senza
        lasciare alcuna traccia nel ledger - solo la richiesta di autenticazione iniziale vi
        compariva. Un diniego e' comunque un evento di sicurezza (vedi ROADMAP.md, F1),
        distinto da AUTHORIZATION_PENDING."""
        import unittest.mock

        skill = FakeSkill()
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core_for_confirmation(registry, AuthGate(passphrase="apri sesamo"))
        core.action_ledger = unittest.mock.Mock()
        core.conversation_state.set_pending_action({
            "intent": "SET_POWER_PLAN", "parameters": {"plan": "balanced", "authenticated": True},
            "reason": "auth_required", "text": "metti il pc in risparmio energetico",
            "trace_id": "trace-from-the-original-request",
        })

        core._handle_confirmation("password sbagliata")

        core.action_ledger.record.assert_called_once()
        (receipt,), kwargs = core.action_ledger.record.call_args
        self.assertEqual(receipt.trace_id, "trace-from-the-original-request")
        self.assertEqual(receipt.authorization, "denied")
        self.assertEqual(receipt.result, "denied_auth")
        self.assertFalse(kwargs["private"])

    def test_confirmed_execution_writes_a_ledger_receipt_correlated_to_the_pending_action(self):
        """F1: prima di questa correzione, _finalize_pending_action eseguiva l'azione vera (qui,
        dopo la passphrase corretta) senza scriverne mai una ricevuta - solo la richiesta di
        conferma iniziale finiva nel ledger, non l'esecuzione. Il trace_id impostato da chi ha
        chiesto la conferma deve comparire identico sulla ricevuta finale."""
        import unittest.mock

        skill = FakeSkill(SkillResult(success=True, data={"plan": "balanced"}))
        registry = FakeRegistry({"SET_POWER_PLAN": skill})
        core = _bare_core_for_confirmation(registry, AuthGate(passphrase="apri sesamo"))
        core.action_ledger = unittest.mock.Mock()
        core.conversation_state.set_pending_action({
            "intent": "SET_POWER_PLAN", "parameters": {"plan": "balanced", "authenticated": True},
            "reason": "auth_required", "text": "metti il pc in risparmio energetico",
            "trace_id": "trace-from-the-original-request",
        })

        with unittest.mock.patch("core.jake_core.log_action"):
            core._handle_confirmation("apri sesamo")

        core.action_ledger.record.assert_called_once()
        (receipt,), kwargs = core.action_ledger.record.call_args
        self.assertEqual(receipt.trace_id, "trace-from-the-original-request")
        self.assertEqual(receipt.authorization, "passphrase")
        self.assertEqual(receipt.result, "success")
        self.assertFalse(kwargs["private"])


class HandleConfirmationDenialTests(unittest.TestCase):
    """F1: un "no" a una richiesta di conferma normale (non ADMIN/auth_required) e' un diniego
    quanto una passphrase sbagliata, e merita la stessa ricevuta nel ledger - vedi ROADMAP.md,
    F1 ("un diniego e' comunque un evento di sicurezza degno di una ricevuta")."""

    def test_negative_answer_writes_a_denied_receipt_to_the_ledger(self):
        import unittest.mock

        skill = FakeSkill()
        registry = FakeRegistry({"DELETE_PATH": skill})
        core = _bare_core_for_confirmation(registry, AuthGate())
        core.action_ledger = unittest.mock.Mock()
        core.conversation_state.set_pending_action({
            "intent": "DELETE_PATH", "parameters": {"path": "C:/tmp/foo", "confirmed": True},
            "reason": "confirmation_required", "text": "cancella C:/tmp/foo",
            "trace_id": "trace-from-the-original-request",
        })

        response = core._handle_confirmation("no")

        self.assertEqual(skill.calls, [])
        self.assertIn("annullato", response.lower())
        self.assertFalse(core.conversation_state.has_pending_action())
        core.action_ledger.record.assert_called_once()
        (receipt,), kwargs = core.action_ledger.record.call_args
        self.assertEqual(receipt.trace_id, "trace-from-the-original-request")
        self.assertEqual(receipt.authorization, "denied")
        self.assertEqual(receipt.result, "denied_confirmation")
        self.assertFalse(kwargs["private"])


class PendingActionPolicyTests(unittest.TestCase):
    """F1.2.5: il consenso non congela i permessi e una busta non prova l'identita'."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.skill = FakeSkill()
        self.registry = FakeRegistry({"SET_POWER_PLAN": self.skill, "DELETE_PATH": self.skill})
        self.core = _bare_core_for_confirmation(self.registry, AuthGate(passphrase="fixture passphrase"))
        self.core.action_ledger = ActionLedger(Path(tmp.name) / "ledger.jsonl")
        patcher = mock.patch("core.jake_core.log_action")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pending(self, intent="DELETE_PATH", reason="confirmation_required", **parameters):
        self.core.conversation_state.set_pending_action({
            "intent": intent, "parameters": parameters, "reason": reason,
            "text": "richiesta fixture", "trace_id": "original-trace",
        })

    def test_policy_revoked_after_prompt_blocks_confirmed_execution_and_records_denial(self):
        self._pending(path="fixture.txt", confirmed=True)
        self.core.policy_engine.blocked_intents.add("DELETE_PATH")

        self.core._handle_confirmation("si")

        self.assertEqual(self.skill.calls, [])
        self.assertFalse(self.core.conversation_state.has_pending_action())
        (receipt,) = self.core.action_ledger.read_all()
        self.assertEqual(receipt["trace_id"], "original-trace")
        self.assertEqual(receipt["authorization"], "blocked")
        self.assertEqual(receipt["error_category"], "denied")

    def test_new_auth_requirement_after_prompt_waits_for_real_authentication(self):
        self._pending("SET_POWER_PLAN", plan="balanced", confirmed=True)
        self.core.policy_engine.require_auth_intents.add("SET_POWER_PLAN")

        self.core._handle_confirmation("si")

        self.assertEqual(self.skill.calls, [])
        pending = self.core.conversation_state.get_pending_action()
        self.assertEqual(pending["reason"], "auth_required")
        self.assertEqual(pending["trace_id"], "original-trace")
        self.assertEqual(self.core.action_ledger.read_all()[0]["authorization"], "pending")

    def test_confirmation_envelope_cannot_smuggle_authentication(self):
        self.core.policy_engine.require_auth_intents.add("SET_POWER_PLAN")
        self._pending(
            "SET_POWER_PLAN", plan="balanced", confirmed=True,
            authenticated=True, authenticated_via="windows_hello",
        )

        self.core._handle_confirmation("si")

        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.core.conversation_state.get_pending_action()["reason"], "auth_required")

    def test_valid_passphrase_grants_both_identity_and_consent_with_trusted_provenance(self):
        self.core.policy_engine.require_auth_intents.add("SET_POWER_PLAN")
        self.core.policy_engine.always_confirm_intents.add("SET_POWER_PLAN")
        self._pending(
            "SET_POWER_PLAN", reason="auth_required", plan="balanced",
            authenticated_via="windows_hello", authenticated=True,
        )

        self.core._handle_confirmation("fixture passphrase")

        self.assertEqual(self.skill.calls, [{
            "plan": "balanced", "confirmed": True,
            "authenticated": True, "authenticated_via": "passphrase",
        }])
        self.assertFalse(self.core.conversation_state.has_pending_action())
        (receipt,) = self.core.action_ledger.read_all()
        self.assertEqual(receipt["authorization"], "passphrase")

    def test_resumption_keeps_the_approved_target_without_rewrite_or_fallback(self):
        self._pending(path="fixture.txt")
        with mock.patch("core.jake_core.fallbacks.pre_execution_rewrite") as rewrite:
            self.core._handle_confirmation("si")

        rewrite.assert_not_called()
        self.assertEqual(self.skill.calls, [{"path": "fixture.txt", "confirmed": True}])

    def test_private_mode_does_not_persist_revoked_action(self):
        self.core.private_mode = True
        self._pending(path="fixture.txt", confirmed=True)
        self.core.policy_engine.blocked_intents.add("DELETE_PATH")

        self.core._handle_confirmation("si")

        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.core.action_ledger.read_all(), [])


class FakeLoggerCapturingWarnings:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)

    def exception(self, *args, **kwargs):
        pass


class SafeConfirmEnvelopeTests(unittest.TestCase):
    """F1: JakeCore._safe_confirm_envelope valida la busta CONFIRMATION_REQUIRED/AUTH_REQUIRED
    di una skill (core/schema_validation.py) prima di fidarsene - vedi anche
    tests/test_schema_validation.py per la validazione in isolamento."""

    def _core(self):
        core = JakeCore.__new__(JakeCore)
        core.skill_registry = FakeRegistry({"FORGET": FakeSkill()})
        core.logger = FakeLoggerCapturingWarnings()
        return core

    def test_well_formed_envelope_passes_through_unchanged(self):
        core = self._core()
        result = SkillResult(
            success=False, data={"message": "Confermi?", "confirm_parameters": {"confirmed": True}},
            error="CONFIRMATION_REQUIRED",
        )

        envelope = core._safe_confirm_envelope("FORGET", {"topic": "tutto"}, result, "confirmation_required")

        self.assertEqual(envelope, result.data)
        self.assertEqual(core.logger.warnings, [])

    def test_missing_fields_fall_back_to_a_safe_default_with_the_confirmed_marker(self):
        core = self._core()
        result = SkillResult(success=False, data={}, error="CONFIRMATION_REQUIRED")  # busta vuota, malformata

        envelope = core._safe_confirm_envelope("FORGET", {"topic": "tutto"}, result, "confirmation_required")

        self.assertEqual(envelope["confirm_parameters"], {"topic": "tutto", "confirmed": True})
        self.assertTrue(envelope["message"])
        self.assertEqual(len(core.logger.warnings), 1)
        self.assertIn("FORGET", core.logger.warnings[0])

    def test_auth_required_fallback_uses_the_authenticated_marker_not_confirmed(self):
        core = self._core()
        result = SkillResult(success=False, data={"confirm_parameters": "non un dict"}, error="AUTH_REQUIRED")

        envelope = core._safe_confirm_envelope("FORGET", {"topic": "tutto"}, result, "auth_required")

        self.assertEqual(envelope["confirm_parameters"], {"topic": "tutto", "authenticated": True})
        self.assertNotIn("confirmed", envelope["confirm_parameters"])

    def test_malformed_envelope_never_raises(self):
        core = self._core()
        for bad_data in (None, "una stringa", 42, ["una", "lista"]):
            result = SkillResult(success=False, data=bad_data, error="CONFIRMATION_REQUIRED")
            envelope = core._safe_confirm_envelope("FORGET", {}, result, "confirmation_required")
            self.assertIn("message", envelope)
            self.assertIn("confirm_parameters", envelope)


class FakeRetriever:
    def __init__(self):
        self.refreshed = False

    def refresh(self):
        self.refreshed = True


class FakeDraft:
    def __init__(self, intent, examples=None):
        self.intent = intent
        self.examples = examples or []


class OnSkillInstalledGateWiringTests(unittest.TestCase):
    """F1: always_confirm_intents/require_auth_intents si popolano una sola volta in
    JakeCore.__init__, leggendo self.skill_registry.skills COM'ERA in quel momento (vedi sopra
    in JakeCore, subito dopo la registrazione delle skill built-in/plugin). Una skill installata
    piu' tardi dalla Skill Forge (JakeCore._on_skill_installed, chiamata da core/skill_forge.py
    dopo install()) non ci finiva mai dentro: risk_of() classifica un intent non censito come
    ADMIN per difetto (core/risk.py), ma senza aggiornare questi due insiemi anche qui,
    _resolve_and_execute non lo sapeva ed eseguiva la skill appena scritta da un modello -
    codice mai rivisto da un umano - SENZA conferma ne' autenticazione al primo utilizzo."""

    def _core(self, skill_registry, auth_gate=None):
        core = JakeCore.__new__(JakeCore)
        core.skill_registry = skill_registry
        core.auth_gate = auth_gate or AuthGate()
        core.policy_engine = PolicyEngine(auth_gate=core.auth_gate)
        core.retriever = FakeRetriever()
        core.learning = FakeLearning()
        return core

    def test_newly_forged_unclassified_intent_is_added_to_both_gates(self):
        core = self._core(FakeRegistry({}))

        core._on_skill_installed(FakeDraft("SOME_BRAND_NEW_FORGED_SKILL"))

        self.assertIn("SOME_BRAND_NEW_FORGED_SKILL", core.policy_engine.always_confirm_intents)
        self.assertIn("SOME_BRAND_NEW_FORGED_SKILL", core.policy_engine.require_auth_intents)
        self.assertTrue(core.retriever.refreshed)

    def test_newly_forged_skill_is_actually_gated_not_just_listed(self):
        """Non basta che l'intent finisca negli insiemi giusti: deve anche impedire davvero
        l'esecuzione diretta, come per qualunque altro intent ADMIN gia' noto a risk.py."""
        skill = FakeSkill(SkillResult(success=True, data={}))
        registry = FakeRegistry({"SOME_BRAND_NEW_FORGED_SKILL": skill})
        core = self._core(registry)
        core._on_skill_installed(FakeDraft("SOME_BRAND_NEW_FORGED_SKILL"))

        execution = core._resolve_and_execute(Command("SOME_BRAND_NEW_FORGED_SKILL", {}))
        result = execution.result

        self.assertEqual(skill.calls, [], "la skill forgiata non deve eseguire prima di conferma/auth")
        self.assertIsNotNone(result)
        self.assertFalse(result.success)

    def test_read_only_intent_is_not_added_to_either_gate(self):
        """La correzione non deve trasformare ogni skill installata in un ADMIN a prescindere:
        un intent gia' censito come READ_ONLY in core/risk.py resta libero da conferma/auth
        (qui GET_TIME serve solo a testare la classificazione esistente, non e' realistico che
        la Forge lo rigeneri davvero: gli intent nuovi restano ADMIN per difetto)."""
        core = self._core(FakeRegistry({}))

        core._on_skill_installed(FakeDraft("GET_TIME"))

        self.assertNotIn("GET_TIME", core.policy_engine.always_confirm_intents)
        self.assertNotIn("GET_TIME", core.policy_engine.require_auth_intents)


if __name__ == "__main__":
    unittest.main()
