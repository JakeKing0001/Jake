"""Test unitari per il vecchio esecutore a piano fisso (core/plan_executor.py), il ripiego usato
da JakeCore._try_plan quando l'agente a passi non conclude nulla, e da TriggerScheduler per le
automazioni. Copre anche il logging strutturato (F0) aggiunto qui: stesso schema/stesso
trace_id condiviso dell'agente a passi (vedi tests/test_agent.py::StructuredLoggingTests), non
duplicato altrove prima d'ora - non esisteva ancora un test dedicato a questo modulo."""
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from core.plan_executor import PlanExecutor
from core.planner import Plan, PlanStep
from core.skill_result import SkillResult


class FakeRegistry:
    """CREATE_PATH tocca davvero il filesystem (serve a verify_effect), ADD_NOTE pesca da una
    coda di risultati gia' pronti - stesso schema di FakeRegistry in tests/test_agent.py."""

    def __init__(self, add_note_results: list = None):
        self._add_note_queue = list(add_note_results or [])
        self.calls = []

    def execute(self, intent, parameters=None):
        parameters = parameters or {}
        self.calls.append((intent, dict(parameters)))
        if intent == "CREATE_PATH":
            Path(parameters["path"]).touch()
            return SkillResult(success=True, data={"path": parameters["path"]})
        if intent == "DELETE_PATH":
            Path(parameters["path"]).unlink(missing_ok=True)
            return SkillResult(success=True, data={"path": parameters["path"]})
        if intent == "ADD_NOTE":
            return self._add_note_queue.pop(0)
        raise AssertionError(f"intent non atteso nel test: {intent}")


class PolicyTests(unittest.TestCase):
    def test_blocked_intent_stops_the_plan_without_executing_it(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        outcome = PlanExecutor(registry).execute(plan, blocked_intents={"ADD_NOTE"})

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stopped_step.result.error, "POLICY_BLOCKED")
        self.assertEqual(registry.calls, [])

    def test_always_confirm_intent_pauses_without_executing_it(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        outcome = PlanExecutor(registry).execute(plan, always_confirm_intents={"ADD_NOTE"})

        self.assertEqual(outcome.stopped_step.result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(registry.calls, [])


class IndependentVerificationTests(unittest.TestCase):
    def test_success_claim_without_real_effect_is_downgraded_to_verification_failed(self):
        class LyingRegistry(FakeRegistry):
            def execute(self, intent, parameters=None):
                self.calls.append((intent, parameters))
                return SkillResult(success=True, data={"path": parameters["path"]})

        registry = LyingRegistry()
        missing_path = str(Path(tempfile.gettempdir()) / "jake_test_plan_executor_missing_7712.txt")
        plan = Plan(steps=[PlanStep(intent="CREATE_PATH", parameters={"path": missing_path})])

        outcome = PlanExecutor(registry).execute(plan)

        self.assertEqual(outcome.stopped_step.result.error, "VERIFICATION_FAILED")


class StructuredLoggingTests(unittest.TestCase):
    def test_verifiable_intent_records_verified_true_on_real_success(self):
        registry = FakeRegistry()
        target = str(Path(tempfile.gettempdir()) / "jake_test_plan_executor_log_3381.txt")
        self.addCleanup(lambda: Path(target).unlink(missing_ok=True))
        plan = Plan(steps=[PlanStep(intent="CREATE_PATH", parameters={"path": target})])

        with unittest.mock.patch("core.plan_executor.log_action") as mock_log:
            PlanExecutor(registry).execute(plan, trace_id="trace-plan-1", model="qwen2.5:7b")

        mock_log.assert_called_once()
        _, kwargs = mock_log.call_args
        self.assertEqual(mock_log.call_args.args[0], "trace-plan-1")
        self.assertEqual(kwargs["skill"], "CREATE_PATH")
        self.assertEqual(kwargs["verified"], True)
        self.assertEqual(kwargs["result"], "success")
        self.assertEqual(kwargs["model"], "qwen2.5:7b")

    def test_non_verifiable_intent_leaves_verified_absent(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        with unittest.mock.patch("core.plan_executor.log_action") as mock_log:
            PlanExecutor(registry).execute(plan)

        self.assertIsNone(mock_log.call_args.kwargs["verified"])

    def test_multiple_steps_share_the_same_trace_id(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        target = str(Path(tempfile.gettempdir()) / "jake_test_plan_executor_shared_4471.txt")
        self.addCleanup(lambda: Path(target).unlink(missing_ok=True))
        plan = Plan(steps=[
            PlanStep(intent="ADD_NOTE", parameters={"text": "x"}),
            PlanStep(intent="CREATE_PATH", parameters={"path": target}),
        ])

        with unittest.mock.patch("core.plan_executor.log_action") as mock_log:
            PlanExecutor(registry).execute(plan, trace_id="shared-plan-trace")

        self.assertEqual(mock_log.call_count, 2)
        trace_ids_used = {call.args[0] for call in mock_log.call_args_list}
        self.assertEqual(trace_ids_used, {"shared-plan-trace"})

    def test_private_flag_is_forwarded(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        with unittest.mock.patch("core.plan_executor.log_action") as mock_log:
            PlanExecutor(registry).execute(plan, private=True)

        self.assertTrue(mock_log.call_args.kwargs["private"])

    def test_blocked_intent_is_logged_too(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        with unittest.mock.patch("core.plan_executor.log_action") as mock_log:
            PlanExecutor(registry).execute(plan, blocked_intents={"ADD_NOTE"})

        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args.kwargs["result"], "policy_blocked")


class SessionRecorderWiringTests(unittest.TestCase):
    """Vedi tests/test_agent.py::SessionRecorderWiringTests: stesso principio, altro esecutore."""

    def test_failed_step_calls_record_failure_with_the_real_parameters(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=False, data={}, error="MISSING_PARAMETERS")])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "prova"})])
        recorder = unittest.mock.Mock()
        executor = PlanExecutor(registry)
        executor.session_recorder = recorder

        # log_action mascherato: non e' quello sotto test qui (vedi StructuredLoggingTests) e,
        # se non mascherato, scriverebbe davvero su data/jake_actions.jsonl del contributore.
        with unittest.mock.patch("core.plan_executor.log_action"):
            executor.execute(plan, trace_id="trace-plan-fail")

        recorder.record_failure.assert_called_once()
        args, kwargs = recorder.record_failure.call_args
        self.assertEqual(args[0], "trace-plan-fail")
        self.assertEqual(kwargs["intent"], "ADD_NOTE")
        self.assertEqual(kwargs["parameters"], {"text": "prova"})
        self.assertEqual(kwargs["error"], "error:MISSING_PARAMETERS")

    def test_successful_step_does_not_call_record_failure(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "prova"})])
        recorder = unittest.mock.Mock()
        executor = PlanExecutor(registry)
        executor.session_recorder = recorder

        with unittest.mock.patch("core.plan_executor.log_action"):
            executor.execute(plan)

        recorder.record_failure.assert_not_called()

    def test_confirmation_required_does_not_call_record_failure(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        recorder = unittest.mock.Mock()
        executor = PlanExecutor(registry)
        executor.session_recorder = recorder

        with unittest.mock.patch("core.plan_executor.log_action"):
            executor.execute(plan, always_confirm_intents={"ADD_NOTE"})

        recorder.record_failure.assert_not_called()


class ActionLedgerWiringTests(unittest.TestCase):
    """F1 (Trustworthy Agent Core 3.0, vedi core/action_ledger.py): vedi anche
    tests/test_agent.py::ActionLedgerWiringTests, stesso principio per l'altro esecutore."""

    def test_step_records_a_receipt_with_the_given_requested_by(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        ledger = unittest.mock.Mock()
        executor = PlanExecutor(registry)
        executor.action_ledger = ledger

        with unittest.mock.patch("core.plan_executor.log_action"):
            executor.execute(plan, trace_id="trace-plan-ledger", requested_by="trigger:buonanotte")

        ledger.record.assert_called_once()
        (receipt,), kwargs = ledger.record.call_args
        self.assertEqual(receipt.trace_id, "trace-plan-ledger")
        self.assertEqual(receipt.requested_by, "trigger:buonanotte")
        self.assertEqual(receipt.authorization, "none")
        self.assertFalse(kwargs["private"])

    def test_requested_by_defaults_to_user(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        ledger = unittest.mock.Mock()
        executor = PlanExecutor(registry)
        executor.action_ledger = ledger

        with unittest.mock.patch("core.plan_executor.log_action"):
            executor.execute(plan)

        self.assertEqual(ledger.record.call_args.args[0].requested_by, "user")


class KillSwitchStopsThePlanTests(unittest.TestCase):
    """F1 (vedi core/kill_switch.py e tests/test_agent.py::KillSwitchStopsTheRunTests, stesso
    principio per l'altro esecutore): il flag e' controllato solo tra un passo e il successivo."""

    def test_activating_during_step_one_stops_before_step_two_and_rolls_back(self):
        target = str(Path(tempfile.gettempdir()) / "jake_test_plan_kill_9231.txt")
        self.addCleanup(lambda: Path(target).unlink(missing_ok=True))

        class KillingRegistry(FakeRegistry):
            """Simula il kill switch premuto MENTRE il passo 1 e' in corso: si attiva come
            effetto collaterale dell'esecuzione del primo passo, non prima."""

            def __init__(self, kill_switch):
                super().__init__()
                self.kill_switch = kill_switch

            def execute(self, intent, parameters=None):
                result = super().execute(intent, parameters)
                self.kill_switch.activate()
                return result

        from core.kill_switch import KillSwitch
        kill_switch = KillSwitch()
        registry = KillingRegistry(kill_switch)
        plan = Plan(steps=[
            PlanStep(intent="CREATE_PATH", parameters={"path": target}),
            PlanStep(intent="ADD_NOTE", parameters={"text": "non deve mai arrivare qui"}),
        ])
        executor = PlanExecutor(registry)
        executor.kill_switch = kill_switch

        outcome = executor.execute(plan)

        # CREATE_PATH (passo 1) poi DELETE_PATH (il rollback che lo annulla): ADD_NOTE (passo 2)
        # non compare mai, il kill switch l'ha bloccato prima che l'esecutore ci arrivasse.
        self.assertEqual([call[0] for call in registry.calls], ["CREATE_PATH", "DELETE_PATH"])
        self.assertEqual(outcome.stopped_step.result.error, "KILLED")
        self.assertEqual(len(outcome.rolled_back), 1)
        self.assertFalse(Path(target).exists(), "il rollback doveva cancellare il file creato dal passo 1")

    def test_already_active_before_the_plan_starts_executes_no_step(self):
        from core.kill_switch import KillSwitch
        kill_switch = KillSwitch()
        kill_switch.activate()
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        executor = PlanExecutor(registry)
        executor.kill_switch = kill_switch

        outcome = executor.execute(plan)

        self.assertEqual(outcome.stopped_step.result.error, "KILLED")
        self.assertEqual(registry.calls, [])


if __name__ == "__main__":
    unittest.main()
