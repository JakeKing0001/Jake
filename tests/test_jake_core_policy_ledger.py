"""F1.2.6: decisioni effettive fino al ledger, con core/agente reali e I/O temporaneo."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import execution_safety
from core.agent import TaskAgent
from core.auth_gate import AuthGate
from core.command import Command
from core.policy_engine import (
    POLICY_REASON_ALLOWED, POLICY_REASON_BLOCKED, POLICY_REASON_CONFIRM, POLICY_REASON_REQUIRE_AUTH,
    POLICY_REASONS,
)
from core.request_context import reset_current_device_id, set_current_device_id
from core.skill_result import SkillResult
from skills.create_path import CreatePathSkill
from tests.test_agent import FakeRetriever, ScriptedOllamaClient
from tests.test_jake_core_pipeline import FakeRegistry, FakeSkill, _bare_core


class AgentRegistry(FakeRegistry):
    def list_capabilities(self):
        names = {"ADD_NOTE": "text", "OPEN_APP": "app", "CREATE_PATH": "path"}
        return [{
            "intent": intent, "description": "Fixture.", "parameters": {
                names[intent]: {"type": "string", "required": True},
            },
        } for intent in self._skills if intent in names]


class PolicyLedgerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.skill = FakeSkill()
        self.registry = AgentRegistry({
            "ADD_NOTE": self.skill, "GET_TIME": self.skill, "OPEN_APP": self.skill,
            "OPEN_URL": self.skill, "CREATE_PATH": CreatePathSkill(),
        })
        self.core = _bare_core(skill_registry=self.registry, ledger_path=self.root / "ledger.jsonl")
        for target, kwargs in (
            ("core.jake_core.log_action", {}), ("core.agent.log_action", {}),
            ("core.jake_core.fallbacks.pre_execution_rewrite", {"side_effect": lambda cmd, registry: cmd}),
        ):
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def receipts(self):
        return self.core.action_ledger.read_all()

    def execute(self, intent="ADD_NOTE", parameters=None):
        return self.core._execute_command("richiesta fixture", Command(intent, parameters or {"text": "fixture"}))

    def enable_auth(self, hello=None):
        gate = AuthGate(passphrase="fixture passphrase", windows_hello_enabled=hello is not None,
                        windows_hello_verify=hello)
        self.core.auth_gate = gate
        self.core.policy_engine.auth_gate = gate
        self.core.policy_engine.require_auth_intents.add("ADD_NOTE")

    def agent(self, intent="ADD_NOTE", parameters=None, raw=False, agent_name="general"):
        client = ScriptedOllamaClient([
            {"action": {"intent": intent, "parameters": parameters if parameters is not None else {"text": "fixture"}}},
            {"action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto."},
        ])
        return TaskAgent(
            self.registry, FakeRetriever([]), client, model_provider=lambda: "fixture-model",
            format_result=lambda name, result: str(result.data),
            executor=self.registry.execute if raw else lambda name, params: self.core._resolve_and_execute(Command(name, params)),
            action_ledger=self.core.action_ledger, fixed_tools=[intent], agent_name=agent_name,
        )

    def test_direct_allow_has_the_actual_reason(self):
        self.execute()
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_ALLOWED)

    def test_direct_block_has_the_policy_reason_without_execution(self):
        self.core.policy_engine.blocked_intents.add("ADD_NOTE")
        self.execute()
        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_BLOCKED)

    def test_denial_keeps_the_original_confirm_reason_even_if_policy_changes(self):
        self.core.policy_engine.always_confirm_intents.add("ADD_NOTE")
        self.execute()
        self.core.policy_engine.always_confirm_intents.clear()
        self.core._handle_confirmation("no")
        pending, denied = self.receipts()
        self.assertEqual(pending["policy_reason"], POLICY_REASON_CONFIRM)
        self.assertEqual(denied["policy_reason"], POLICY_REASON_CONFIRM)
        self.assertEqual(pending["trace_id"], denied["trace_id"])

    def test_wrong_passphrase_keeps_the_original_auth_reason(self):
        self.enable_auth()
        self.execute()
        self.core._handle_confirmation("wrong fixture")
        pending, denied = self.receipts()
        self.assertEqual(pending["policy_reason"], POLICY_REASON_REQUIRE_AUTH)
        self.assertEqual(denied["policy_reason"], POLICY_REASON_REQUIRE_AUTH)
        self.assertEqual(denied["authorization"], "denied")

    def test_confirmed_execution_records_the_new_allow_decision(self):
        self.core.policy_engine.always_confirm_intents.add("ADD_NOTE")
        self.execute()
        self.core._handle_confirmation("si")
        pending, executed = self.receipts()
        self.assertEqual(pending["policy_reason"], POLICY_REASON_CONFIRM)
        self.assertEqual(executed["policy_reason"], POLICY_REASON_ALLOWED)
        self.assertEqual(executed["authorization"], "confirmed")

    def test_authenticated_execution_records_allow_and_real_provenance(self):
        self.enable_auth()
        self.execute()
        self.core._handle_confirmation("fixture passphrase")
        self.assertEqual(self.receipts()[-1]["policy_reason"], POLICY_REASON_ALLOWED)
        self.assertEqual(self.receipts()[-1]["authorization"], "passphrase")

    def test_revocation_after_prompt_records_a_new_block_decision(self):
        self.core.policy_engine.always_confirm_intents.add("ADD_NOTE")
        self.execute()
        self.core.policy_engine.blocked_intents.add("ADD_NOTE")
        self.core._handle_confirmation("si")
        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.receipts()[-1]["policy_reason"], POLICY_REASON_BLOCKED)

    def test_windows_hello_success_records_the_final_allow_decision(self):
        self.enable_auth(hello=lambda reason: True)
        self.execute()
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_ALLOWED)
        self.assertEqual(self.receipts()[0]["authorization"], "windows_hello")

    def test_revocation_during_windows_hello_records_the_final_block_decision(self):
        def verify(reason):
            self.core.policy_engine.blocked_intents.add("ADD_NOTE")
            return True
        self.enable_auth(hello=verify)
        self.execute()
        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_BLOCKED)

    def test_reason_is_captured_before_the_skill_changes_policy(self):
        def execute(parameters):
            self.core.policy_engine.blocked_intents.add("ADD_NOTE")
            return SkillResult(success=True, data={})
        self.skill.execute = execute
        self.execute()
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_ALLOWED)

    def test_successful_fallback_receipt_describes_the_alternative(self):
        self.registry._skills["OPEN_APP"] = FakeSkill(SkillResult(success=False, error="NOT_FOUND"))
        with mock.patch("core.jake_core.fallbacks.alternative_for", return_value=(
            Command("OPEN_URL", {"url": "https://fixture.invalid"}), "ripiego fixture",
        )):
            self.execute("OPEN_APP", {"app": "fixture"})
        (receipt,) = self.receipts()
        self.assertEqual(receipt["intent"], "OPEN_URL")
        self.assertEqual(receipt["policy_reason"], POLICY_REASON_ALLOWED)

    def test_rejected_fallback_does_not_replace_the_original_decision(self):
        self.registry._skills["OPEN_APP"] = FakeSkill(SkillResult(success=False, error="NOT_FOUND"))
        self.core.policy_engine.blocked_intents.add("OPEN_URL")
        with mock.patch("core.jake_core.fallbacks.alternative_for", return_value=(Command("OPEN_URL", {}), None)):
            self.execute("OPEN_APP", {"app": "fixture"})
        (receipt,) = self.receipts()
        self.assertEqual(receipt["intent"], "OPEN_APP")
        self.assertEqual(receipt["policy_reason"], POLICY_REASON_ALLOWED)

    def test_offer_for_another_intent_does_not_invent_a_policy_decision_on_denial(self):
        self.registry._skills["OPEN_APP"] = FakeSkill(SkillResult(success=False, error="NOT_FOUND"))
        with mock.patch("core.jake_core.fallbacks.alternative_for", return_value=(None, None)), mock.patch(
            "core.jake_core.fallbacks.offer_after_failure", return_value={
                "intent": "OTHER_FIXTURE", "parameters": {}, "message": "Confermi altro?",
            },
        ):
            self.execute("OPEN_APP", {"app": "fixture"})
        self.core._handle_confirmation("no")
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_ALLOWED)
        self.assertNotIn("policy_reason", self.receipts()[1])

    def test_unknown_skill_without_execution_gate_has_no_reason(self):
        self.execute("MISSING_FIXTURE")
        self.assertNotIn("policy_reason", self.receipts()[0])

    def test_execution_metadata_is_not_inserted_into_skill_data_or_parameters(self):
        execution = self.core._resolve_and_execute(Command("ADD_NOTE", {"text": "fixture"}))
        self.assertEqual(execution.policy_reason, POLICY_REASON_ALLOWED)
        self.assertEqual(execution.result.data, {})
        self.assertEqual(self.skill.calls, [{"text": "fixture"}])

    def test_untrusted_envelope_cannot_inject_a_secret_as_policy_reason(self):
        self.skill.result = SkillResult(success=False, error="CONFIRMATION_REQUIRED", data={
            "message": "Confermi?", "confirm_parameters": {"text": "fixture", "confirmed": True},
            "policy_reason": "secret-fixture-token",
        })
        self.execute()
        self.core._handle_confirmation("no")
        self.assertTrue(all(r["policy_reason"] == POLICY_REASON_ALLOWED for r in self.receipts()))
        self.assertNotIn("secret-fixture-token", (self.root / "ledger.jsonl").read_text(encoding="utf-8"))

    def test_private_mode_does_not_write_policy_receipts(self):
        self.core.private_mode = True
        self.execute()
        self.assertEqual(self.receipts(), [])

    def test_private_mode_does_not_write_policy_receipts_for_the_agent_path(self):
        """F1.7.8 ("testare modalita' privata end-to-end su tutti i nuovi record"): a differenza
        del test sopra (percorso diretto, JakeCore._execute_command), TaskAgent._log_step scrive
        sul ledger per conto proprio - un chokepoint diverso, con la propria gestione di
        `private`. Il test esistente in tests/test_agent.py::ActionLedgerWiringTests verificava
        solo che record() venisse CHIAMATO con private=True (un Mock), non che un ledger vero
        restasse vuoto per un intent BLOCCATO (che avrebbe popolato policy_reason se non fosse
        privato)."""
        self.core.policy_engine.blocked_intents.add("ADD_NOTE")
        self.agent().run("fixture", private=True)
        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.receipts(), [])

    def test_all_agent_specializations_preserve_the_actual_policy_reason(self):
        for name in ("general", "coding", "research"):
            self.agent(agent_name=name).run("fixture", trace_id=name)
        receipts = self.receipts()
        self.assertEqual({r["requested_by"] for r in receipts}, {"agent:general", "agent:coding", "agent:research"})
        self.assertTrue(all(r["policy_reason"] == POLICY_REASON_ALLOWED for r in receipts))

    def test_agent_block_records_the_reason(self):
        self.core.policy_engine.blocked_intents.add("ADD_NOTE")
        self.agent().run("fixture")
        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_BLOCKED)

    def test_agent_authentication_preserves_actual_authorization_parameters(self):
        self.enable_auth(hello=lambda reason: True)
        outcome = self.agent().run("fixture")
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_ALLOWED)
        self.assertEqual(self.receipts()[0]["authorization"], "windows_hello")
        self.assertEqual(outcome.steps[0].parameters["authenticated_via"], "windows_hello")

    def test_agent_confirmation_and_denial_keep_reason_and_original_trace(self):
        self.core.policy_engine.always_confirm_intents.add("ADD_NOTE")
        agent = self.agent()
        self.core.orchestrator = mock.Mock(run=agent.run)
        self.core._run_agent("fixture")
        self.core._handle_confirmation("no")
        pending, denied = self.receipts()
        self.assertEqual(pending["policy_reason"], POLICY_REASON_CONFIRM)
        self.assertEqual(denied["policy_reason"], POLICY_REASON_CONFIRM)
        self.assertEqual(pending["trace_id"], denied["trace_id"])

    def test_agent_auth_prompt_and_final_execution_keep_reason_and_trace(self):
        self.enable_auth()
        self.core.orchestrator = mock.Mock(run=self.agent().run)
        self.core._run_agent("fixture")
        self.core._handle_confirmation("fixture passphrase")
        pending, executed = self.receipts()
        self.assertEqual(pending["policy_reason"], POLICY_REASON_REQUIRE_AUTH)
        self.assertEqual(executed["policy_reason"], POLICY_REASON_ALLOWED)
        self.assertEqual(pending["trace_id"], executed["trace_id"])

    def test_agent_verifies_the_actual_fallback_intent_not_the_requested_one(self):
        target = self.root / "created.txt"
        self.registry._skills["OPEN_APP"] = FakeSkill(SkillResult(success=False, error="NOT_FOUND"))
        with mock.patch("core.jake_core.fallbacks.alternative_for", return_value=(
            Command("CREATE_PATH", {"path": str(target)}), "ripiego fixture",
        )):
            outcome = self.agent("OPEN_APP", {"app": "fixture"}).run("fixture")
        (receipt,) = self.receipts()
        self.assertTrue(target.exists())
        self.assertEqual(outcome.steps[0].intent, "CREATE_PATH")
        self.assertEqual(receipt["intent"], "CREATE_PATH")
        self.assertEqual(receipt["verified"], "verified")
        self.assertEqual(receipt["policy_reason"], POLICY_REASON_ALLOWED)

    def test_retry_keeps_the_final_policy_decision(self):
        def fail_and_revoke(parameters):
            self.core.policy_engine.blocked_intents.add("CREATE_PATH")
            return SkillResult(success=False, error="OPERATION_FAILED")
        skill = FakeSkill()
        skill.execute = mock.Mock(side_effect=fail_and_revoke)
        self.registry._skills["CREATE_PATH"] = skill
        outcome = self.agent("CREATE_PATH", {"path": str(self.root / "fixture.txt")}).run("fixture")
        self.assertEqual(outcome.steps[0].attempts, 2)
        skill.execute.assert_called_once()
        self.assertEqual(self.receipts()[0]["policy_reason"], POLICY_REASON_BLOCKED)

    def test_raw_test_executor_does_not_invent_a_policy_reason(self):
        self.agent(raw=True).run("fixture")
        self.assertNotIn("policy_reason", self.receipts()[0])

    def test_missing_parameters_do_not_invent_a_policy_reason(self):
        self.agent(parameters={}).run("fixture")
        self.assertEqual(self.skill.calls, [])
        self.assertNotIn("policy_reason", self.receipts()[0])

    def test_transport_rejects_free_text_reasons_without_echoing_secrets(self):
        with self.assertRaises(ValueError) as raised:
            execution_safety.ActionExecution(Command("ADD_NOTE", {}), None, policy_reason="secret-fixture-token")
        self.assertNotIn("secret-fixture-token", str(raised.exception))

    def test_receipt_reasons_are_always_closed_values(self):
        self.execute()
        self.assertTrue(all(r.get("policy_reason") in POLICY_REASONS for r in self.receipts()))


class _PolicyLedgerFixture(unittest.TestCase):
    """Stesse fixture di PolicyLedgerTests (setUp/execute/agent/enable_auth/receipts), estratte
    qui perche' PolicyLedgerTests stessa ha gia' i propri metodi test_*: ereditare direttamente da
    lei farebbe scoprire ed eseguire anche quelli sotto ogni sottoclasse, duplicandoli. Nessun
    metodo test_* qui: solo fixture, come le classi base _With*/*TestCase gia' usate altrove in
    questa sessione (es. tests/test_trigger_manager.py::_WithManager)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.skill = FakeSkill()
        self.registry = AgentRegistry({
            "ADD_NOTE": self.skill, "GET_TIME": self.skill, "OPEN_APP": self.skill,
            "OPEN_URL": self.skill, "CREATE_PATH": CreatePathSkill(),
        })
        self.core = _bare_core(skill_registry=self.registry, ledger_path=self.root / "ledger.jsonl")
        for target, kwargs in (
            ("core.jake_core.log_action", {}), ("core.agent.log_action", {}),
            ("core.jake_core.fallbacks.pre_execution_rewrite", {"side_effect": lambda cmd, registry: cmd}),
        ):
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def receipts(self):
        return self.core.action_ledger.read_all()

    def execute(self, intent="ADD_NOTE", parameters=None):
        return self.core._execute_command("richiesta fixture", Command(intent, parameters or {"text": "fixture"}))

    def enable_auth(self, hello=None):
        gate = AuthGate(passphrase="fixture passphrase", windows_hello_enabled=hello is not None,
                        windows_hello_verify=hello)
        self.core.auth_gate = gate
        self.core.policy_engine.auth_gate = gate
        self.core.policy_engine.require_auth_intents.add("ADD_NOTE")

    def agent(self, intent="ADD_NOTE", parameters=None, raw=False, agent_name="general"):
        client = ScriptedOllamaClient([
            {"action": {"intent": intent, "parameters": parameters if parameters is not None else {"text": "fixture"}}},
            {"action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto."},
        ])
        return TaskAgent(
            self.registry, FakeRetriever([]), client, model_provider=lambda: "fixture-model",
            format_result=lambda name, result: str(result.data),
            executor=self.registry.execute if raw else lambda name, params: self.core._resolve_and_execute(Command(name, params)),
            action_ledger=self.core.action_ledger, fixed_tools=[intent], agent_name=agent_name,
        )


class DeviceIdInReceiptsTests(_PolicyLedgerFixture):
    """F1.2.3/F1.8.1 (fondamenta): il device_id del dispositivo companion che ha originato la
    richiesta (core/request_context.py) arriva nella ricevuta sia per il percorso diretto
    (_log_action_outcome) sia per l'agente a passi (TaskAgent._log_step) - lo stesso meccanismo
    per thread copre entrambi senza bisogno di passare device_id come argomento a ciascuno."""

    def test_direct_command_receipt_carries_the_device_id_set_on_this_thread(self):
        token = set_current_device_id("phone1")
        try:
            self.execute()
        finally:
            reset_current_device_id(token)
        self.assertEqual(self.receipts()[0]["device_id"], "phone1")

    def test_direct_command_receipt_omits_device_id_when_none_is_set(self):
        self.execute()
        self.assertNotIn("device_id", self.receipts()[0])

    def test_agent_step_receipt_carries_the_device_id_set_on_this_thread(self):
        token = set_current_device_id("tablet1")
        try:
            self.agent().run("fixture")
        finally:
            reset_current_device_id(token)
        self.assertEqual(self.receipts()[0]["device_id"], "tablet1")

    def test_denied_action_receipt_carries_the_device_id_set_on_this_thread(self):
        # F1.8.1 (chiusura, uno slot per canale): la richiesta in sospeso e' ora per-canale
        # (core/conversation_state.py), quindi va creata E confermata con lo STESSO device_id -
        # esattamente come accadrebbe per davvero (stessa richiesta HTTP dallo stesso dispositivo).
        self.enable_auth()
        token = set_current_device_id("phone1")
        try:
            self.execute("ADD_NOTE")
            self.core._handle_confirmation("passphrase sbagliata")
        finally:
            reset_current_device_id(token)
        denied = [r for r in self.receipts() if r["result"] == "denied_auth"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["device_id"], "phone1")
