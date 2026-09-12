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
from core.policy_engine import PolicyEngine
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

        outcome = PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine(blocked_intents={"ADD_NOTE"}))

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stopped_step.result.error, "POLICY_BLOCKED")
        self.assertEqual(registry.calls, [])

    def test_always_confirm_intent_pauses_without_executing_it(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        outcome = PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine(always_confirm_intents={"ADD_NOTE"}))

        self.assertEqual(outcome.stopped_step.result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(registry.calls, [])


class PolicyReasonInTheLedgerTests(unittest.TestCase):
    """F1.2.6 ("salvare la motivazione della decisione nel ledger senza salvare segreti"):
    verificato cosa finisce DAVVERO nella ricevuta scritta su disco (ActionLedger vero su file
    temporaneo), non solo cosa la funzione calcola in memoria."""

    def _last_receipt(self, ledger):
        receipts = ledger.read_all()
        self.assertEqual(len(receipts), 1)
        return receipts[0]

    def test_blocked_step_records_why_it_was_blocked(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        with tempfile.TemporaryDirectory() as tmp:
            from core.action_ledger import ActionLedger

            executor = PlanExecutor(registry)
            executor.action_ledger = ActionLedger(path=Path(tmp) / "ledger.jsonl")

            executor.execute(plan, policy_engine=PolicyEngine(blocked_intents={"ADD_NOTE"}))

            self.assertEqual(self._last_receipt(executor.action_ledger)["policy_reason"], "intent_in_blocked_intents")

    def test_confirm_step_records_why_it_needs_confirmation(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        with tempfile.TemporaryDirectory() as tmp:
            from core.action_ledger import ActionLedger

            executor = PlanExecutor(registry)
            executor.action_ledger = ActionLedger(path=Path(tmp) / "ledger.jsonl")

            executor.execute(plan, policy_engine=PolicyEngine(always_confirm_intents={"ADD_NOTE"}))

            self.assertEqual(self._last_receipt(executor.action_ledger)["policy_reason"], "intent_in_always_confirm_intents")

    def test_allowed_step_records_that_no_restriction_matched(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        with tempfile.TemporaryDirectory() as tmp:
            from core.action_ledger import ActionLedger

            executor = PlanExecutor(registry)
            executor.action_ledger = ActionLedger(path=Path(tmp) / "ledger.jsonl")

            executor.execute(plan, policy_engine=PolicyEngine())

            self.assertEqual(self._last_receipt(executor.action_ledger)["policy_reason"], "no_restriction_matched")

    def test_killed_step_has_no_policy_reason_since_no_policy_decision_was_made(self):
        from core.kill_switch import KillSwitch

        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        with tempfile.TemporaryDirectory() as tmp:
            from core.action_ledger import ActionLedger

            executor = PlanExecutor(registry)
            executor.action_ledger = ActionLedger(path=Path(tmp) / "ledger.jsonl")
            executor.kill_switch = KillSwitch()
            executor.kill_switch.activate()

            executor.execute(plan, policy_engine=PolicyEngine())

            self.assertNotIn("policy_reason", self._last_receipt(executor.action_ledger))


class IndependentVerificationTests(unittest.TestCase):
    def test_success_claim_without_real_effect_is_downgraded_to_verification_failed(self):
        class LyingRegistry(FakeRegistry):
            def execute(self, intent, parameters=None):
                self.calls.append((intent, parameters))
                return SkillResult(success=True, data={"path": parameters["path"]})

        registry = LyingRegistry()
        missing_path = str(Path(tempfile.gettempdir()) / "jake_test_plan_executor_missing_7712.txt")
        plan = Plan(steps=[PlanStep(intent="CREATE_PATH", parameters={"path": missing_path})])

        outcome = PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine())

        self.assertEqual(outcome.stopped_step.result.error, "VERIFICATION_FAILED")


class StructuredLoggingTests(unittest.TestCase):
    def test_verifiable_intent_records_verified_true_on_real_success(self):
        registry = FakeRegistry()
        target = str(Path(tempfile.gettempdir()) / "jake_test_plan_executor_log_3381.txt")
        self.addCleanup(lambda: Path(target).unlink(missing_ok=True))
        plan = Plan(steps=[PlanStep(intent="CREATE_PATH", parameters={"path": target})])

        with unittest.mock.patch("core.plan_executor.log_action") as mock_log:
            PlanExecutor(registry).execute(
                plan, policy_engine=PolicyEngine(), trace_id="trace-plan-1", model="qwen2.5:7b",
            )

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
            PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine())

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
            PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine(), trace_id="shared-plan-trace")

        self.assertEqual(mock_log.call_count, 2)
        trace_ids_used = {call.args[0] for call in mock_log.call_args_list}
        self.assertEqual(trace_ids_used, {"shared-plan-trace"})

    def test_private_flag_is_forwarded(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        with unittest.mock.patch("core.plan_executor.log_action") as mock_log:
            PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine(), private=True)

        self.assertTrue(mock_log.call_args.kwargs["private"])

    def test_blocked_intent_is_logged_too(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        with unittest.mock.patch("core.plan_executor.log_action") as mock_log:
            PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine(blocked_intents={"ADD_NOTE"}))

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
            executor.execute(plan, policy_engine=PolicyEngine(), trace_id="trace-plan-fail")

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
            executor.execute(plan, policy_engine=PolicyEngine())

        recorder.record_failure.assert_not_called()

    def test_confirmation_required_does_not_call_record_failure(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        recorder = unittest.mock.Mock()
        executor = PlanExecutor(registry)
        executor.session_recorder = recorder

        with unittest.mock.patch("core.plan_executor.log_action"):
            executor.execute(plan, policy_engine=PolicyEngine(always_confirm_intents={"ADD_NOTE"}))

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
            executor.execute(
                plan, policy_engine=PolicyEngine(), trace_id="trace-plan-ledger",
                requested_by="trigger:buonanotte",
            )

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
            executor.execute(plan, policy_engine=PolicyEngine())

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

        outcome = executor.execute(plan, policy_engine=PolicyEngine())

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


class RollbackRespectsBlockedIntentsTests(unittest.TestCase):
    """F1.2.5 (vedi core/execution_safety.py::rollback_effect, stesso principio di
    tests/test_agent.py::RollbackAfterFatalErrorTests per l'altro esecutore): un rollback non
    deve eseguire un intent che l'utente ha bloccato in config.json, nemmeno per annullare un
    passo gia' approvato."""

    def test_rollback_is_refused_when_the_compensating_intent_is_blocked(self):
        target = str(Path(tempfile.gettempdir()) / "jake_test_plan_rollback_blocked_9231.txt")
        self.addCleanup(lambda: Path(target).unlink(missing_ok=True))
        registry = FakeRegistry(add_note_results=[SkillResult(success=False, data={}, error="MISSING_PARAMETERS")])
        plan = Plan(steps=[
            PlanStep(intent="CREATE_PATH", parameters={"path": target}),
            PlanStep(intent="ADD_NOTE", parameters={"text": "fallisce"}),
        ])
        executor = PlanExecutor(registry)
        policy_engine = PolicyEngine(blocked_intents={"DELETE_PATH"})

        outcome = executor.execute(plan, policy_engine=policy_engine)

        self.assertEqual(outcome.rolled_back, [])
        self.assertTrue(Path(target).exists(), "DELETE_PATH e' bloccato: il rollback non doveva cancellare il file")


class AuthorizationSignalStrippingTests(unittest.TestCase):
    """F1: un piano eseguito qui (il ripiego di JakeCore._try_plan, un'automazione salvata con
    RUN_WORKFLOW, o un trigger che parte da solo) non ha MAI nessuno pronto a confermare in
    tempo reale - a differenza del percorso interattivo di JakeCore. Prima di questa correzione,
    un passo che arrivava GIA' con "confirmed": True dentro ai parametri (es. un planner/LLM
    indotto da un prompt costruito ad arte nella richiesta originale: core/planner_provider.py
    non limita quali chiavi puo' contenere 'parameters') eseguiva SUBITO una skill
    self-confirming come DELETE_PATH - che non finisce mai in always_confirm_intents per design
    (vedi core/risk.py SELF_CONFIRMING_INTENTS) perche' si presume controlli da sola la propria
    conferma - aggirando del tutto la sicurezza che la docstring di PlanExecutor.execute()
    promette di non aggirare mai. Usa la skill VERA (skills/delete_path.py), non una sua
    reimplementazione (vedi tests/test_execution_safety.py per lo stesso principio): solo la
    skill vera controlla davvero parameters.get('confirmed') prima di agire."""

    def _real_delete_registry(self):
        from skills.delete_path import DeletePathSkill

        class RealDeleteRegistry:
            def __init__(self):
                self.skill = DeletePathSkill()

            def execute(self, intent, parameters=None):
                assert intent == "DELETE_PATH"
                return self.skill.execute(parameters or {})

        return RealDeleteRegistry()

    def test_preset_confirmed_parameter_does_not_bypass_a_self_confirming_skill(self):
        target = Path(tempfile.gettempdir()) / "jake_test_plan_auth_strip_9412.txt"
        target.write_text("dati importanti")
        self.addCleanup(lambda: target.unlink(missing_ok=True))

        plan = Plan(steps=[PlanStep(intent="DELETE_PATH", parameters={"path": str(target), "confirmed": True})])

        outcome = PlanExecutor(self._real_delete_registry()).execute(plan, policy_engine=PolicyEngine())

        self.assertFalse(outcome.success, "il passo doveva fermarsi in attesa di conferma, non eseguire")
        self.assertEqual(outcome.stopped_step.result.error, "CONFIRMATION_REQUIRED")
        self.assertTrue(target.exists(), "il file non doveva essere cancellato senza una conferma reale")

    def test_preset_authenticated_parameter_is_also_stripped(self):
        """Stessa protezione per il gradino REQUIRE_AUTH (authenticated/authenticated_via,
        v5.4/5.5): un piano automatico non deve poter auto-autenticarsi piu' di quanto non
        possa auto-confermarsi."""
        target = Path(tempfile.gettempdir()) / "jake_test_plan_auth_strip_9413.txt"
        target.write_text("dati importanti")
        self.addCleanup(lambda: target.unlink(missing_ok=True))

        plan = Plan(steps=[
            PlanStep(intent="DELETE_PATH", parameters={
                "path": str(target), "authenticated": True, "authenticated_via": "windows_hello",
            }),
        ])

        outcome = PlanExecutor(self._real_delete_registry()).execute(plan, policy_engine=PolicyEngine())

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stopped_step.result.error, "CONFIRMATION_REQUIRED")
        self.assertTrue(target.exists())

    def test_the_ledger_reflects_no_real_authorization_not_the_spoofed_one(self):
        """Anche il ledger (F1) non deve mai mostrare 'confirmed'/'passphrase' per un'azione
        che non ha ricevuto conferma. La skill vera chiede consenso: 'pending', non 'none',
        come per lo stesso esito restituito direttamente da PolicyEngine."""
        target = Path(tempfile.gettempdir()) / "jake_test_plan_auth_strip_9414.txt"
        target.write_text("dati importanti")
        self.addCleanup(lambda: target.unlink(missing_ok=True))

        plan = Plan(steps=[PlanStep(intent="DELETE_PATH", parameters={"path": str(target), "confirmed": True})])
        ledger = unittest.mock.Mock()
        executor = PlanExecutor(self._real_delete_registry())
        executor.action_ledger = ledger

        with unittest.mock.patch("core.plan_executor.log_action"):
            executor.execute(plan, policy_engine=PolicyEngine())

        ledger.record.assert_called_once()
        (receipt,), _ = ledger.record.call_args
        self.assertEqual(receipt.authorization, "pending")
        self.assertEqual(receipt.error_category, "pending")
        self.assertTrue(target.exists())

    def test_non_authorization_parameters_of_the_same_step_are_left_untouched(self):
        """La sanificazione toglie solo le chiavi di autorizzazione, non altri parametri
        legittimi dello stesso passo. F1: la funzione vive ora in core/policy_engine.py
        (condivisa con JakeCore, vedi tests/test_policy_engine.py per la copertura completa) -
        qui si verifica solo che PlanExecutor la importi e usi davvero da li'."""
        from core.policy_engine import strip_authorization_signals

        cleaned = strip_authorization_signals({
            "path": "C:/tmp/file.txt", "confirmed": True, "authenticated": True,
            "authenticated_via": "passphrase", "destination": "C:/tmp",
        })

        self.assertEqual(cleaned, {"path": "C:/tmp/file.txt", "destination": "C:/tmp"})


class DryRunTests(unittest.TestCase):
    """F6 ("mostrami prima" - vedi ROADMAP.md): dry_run=True non deve MAI eseguire una skill
    vera, ma deve comunque fermarsi esattamente dove si fermerebbe un run reale (BLOCK/CONFIRM/
    KILLED), cosi' l'utente vede la sequenza VERA che accadrebbe, non una finta ottimistica."""

    def test_allowed_steps_are_simulated_without_touching_the_registry(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[
            PlanStep(intent="ADD_NOTE", parameters={"text": "prova"}),
            PlanStep(intent="ADD_NOTE", parameters={"text": "prova2"}),
        ])

        outcome = PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine(), dry_run=True)

        self.assertEqual(registry.calls, [], "nessuna skill deve essere eseguita davvero in dry-run")
        self.assertTrue(outcome.success)
        self.assertEqual(len(outcome.completed), 2)
        self.assertTrue(outcome.completed[0].result.data["dry_run"])
        self.assertEqual(outcome.completed[0].result.data["intent"], "ADD_NOTE")

    def test_blocked_step_still_stops_the_preview_at_the_same_point(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        outcome = PlanExecutor(registry).execute(
            plan, policy_engine=PolicyEngine(blocked_intents={"ADD_NOTE"}), dry_run=True,
        )

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stopped_step.result.error, "POLICY_BLOCKED")
        self.assertEqual(registry.calls, [])

    def test_step_needing_confirmation_still_stops_the_preview(self):
        registry = FakeRegistry()
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        outcome = PlanExecutor(registry).execute(
            plan, policy_engine=PolicyEngine(always_confirm_intents={"ADD_NOTE"}), dry_run=True,
        )

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stopped_step.result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(registry.calls, [])

    def test_dry_run_never_writes_to_the_ledger(self):
        """Niente e' successo per davvero: una ricevuta nel ledger per un passo simulato
        affermerebbe un'azione mai avvenuta."""
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])
        ledger = unittest.mock.Mock()
        executor = PlanExecutor(registry)
        executor.action_ledger = ledger

        executor.execute(plan, policy_engine=PolicyEngine(), dry_run=True)

        ledger.record.assert_not_called()

    def test_dry_run_stops_at_the_first_blocking_step_not_after(self):
        """Se il primo passo e' bloccato, i successivi non vengono nemmeno simulati - la
        sequenza mostrata deve essere quella che accadrebbe DAVVERO, dove un passo bloccato
        interrompe tutto il resto."""
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[
            PlanStep(intent="DELETE_PATH", parameters={"path": "x"}),
            PlanStep(intent="ADD_NOTE", parameters={"text": "mai raggiunto"}),
        ])

        outcome = PlanExecutor(registry).execute(
            plan, policy_engine=PolicyEngine(blocked_intents={"DELETE_PATH"}), dry_run=True,
        )

        self.assertEqual(outcome.completed, [])
        self.assertEqual(outcome.stopped_step.step.intent, "DELETE_PATH")

    def test_default_dry_run_is_false_and_executes_for_real(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "prova"})])

        PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine())

        self.assertEqual(registry.calls, [("ADD_NOTE", {"text": "prova"})])


class MissingPolicyEngineFailsClosedTests(unittest.TestCase):
    """F1.2.1 (12/09/2026): un chiamante di execute() che ometta policy_engine oggi si ferma
    su ogni passo con POLICY_BLOCKED, invece di eseguire senza alcun controllo. Prima di questa
    correzione policy_engine=None significava ALLOW per qualunque intent (vedi
    docs/action-execution-paths.md, "Nota sul percorso 3") - questo test avrebbe fallito con il
    comportamento precedente: registry.calls non sarebbe stato vuoto, e l'errore non sarebbe
    stato POLICY_BLOCKED. I tre chiamanti reali (JakeCore._try_plan, RunWorkflowSkill,
    TriggerScheduler) passano gia' tutti un policy_engine vero e non sono quindi toccati da
    questo cambiamento - vedi core/jake_core.py, skills/workflow.py, core/trigger_scheduler.py."""

    def test_read_only_step_is_still_blocked_without_a_policy_engine(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "non deve arrivare qui"})])

        outcome = PlanExecutor(registry).execute(plan)

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stopped_step.result.error, "POLICY_BLOCKED")
        self.assertEqual(registry.calls, [], "nessuna skill deve eseguire senza un policy_engine")

    def test_dry_run_is_also_blocked_without_a_policy_engine(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        plan = Plan(steps=[PlanStep(intent="ADD_NOTE", parameters={"text": "x"})])

        outcome = PlanExecutor(registry).execute(plan, dry_run=True)

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stopped_step.result.error, "POLICY_BLOCKED")


if __name__ == "__main__":
    unittest.main()
