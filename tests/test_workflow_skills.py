"""Test unitari per skills/workflow.py (SAVE_WORKFLOW/RUN_WORKFLOW). Il modulo non aveva
ancora nessuna suite dedicata - esattamente come e' potuto restare inosservato il buco reale
verificato qui sotto (F1, vedi ROADMAP.md): RunWorkflowSkill non passava mai policy_engine a
PlanExecutor.execute(), che senza di esso non applica nessun controllo (core/policy_engine.py).
Un'automazione con un passo DESTRUCTIVE/ADMIN non self-confirming (es. FORGET) eseguiva quel
passo senza alcuna conferma, con un comando diretto dell'utente - non serviva nemmeno un prompt
costruito ad arte."""
import unittest

from core.plan_executor import PlanExecutor
from core.planner import Plan, PlanStep
from core.policy_engine import PolicyEngine
from core.skill_result import SkillResult
from skills.workflow import RunWorkflowSkill, SaveWorkflowSkill


class FakeWorkflowManager:
    def __init__(self, plans: dict = None):
        self._plans = dict(plans or {})
        self.saved = []

    def load(self, name):
        return self._plans.get(name)

    def save(self, name, plan):
        self.saved.append((name, plan))
        self._plans[name] = plan


class FakePlanExecutor:
    def __init__(self, outcome=None):
        self.outcome = outcome or object()
        self.calls = []

    def execute(self, plan, **kwargs):
        self.calls.append((plan, kwargs))
        return self.outcome


class _RecordingRegistry:
    """Registro minimo per il PlanExecutor VERO sotto - registra ogni intent eseguito davvero,
    cosi' un test puo' provare che un passo bloccato non arriva mai a toccare la skill."""

    def __init__(self):
        self.executed_intents: list[str] = []

    def execute(self, intent, parameters=None, policy_engine=None):
        self.executed_intents.append(intent)
        return SkillResult(success=True, data={})


class FakePlannerProvider:
    def __init__(self, plan=None):
        self.plan = plan
        self.requests = []

    def build_plan(self, request):
        self.requests.append(request)
        return self.plan


class RunWorkflowSkillTests(unittest.TestCase):
    def test_missing_name_is_reported(self):
        skill = RunWorkflowSkill(FakeWorkflowManager(), FakePlanExecutor())

        result = skill.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_unknown_workflow_is_reported(self):
        skill = RunWorkflowSkill(FakeWorkflowManager(), FakePlanExecutor())

        result = skill.execute({"name": "non esiste"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_forwards_the_policy_engine_to_the_executor(self):
        """Il buco reale: senza questo, un'automazione con un passo DESTRUCTIVE/ADMIN non
        self-confirming (es. FORGET) eseguiva senza alcuna conferma."""
        plan = Plan(steps=[PlanStep(intent="FORGET", parameters={"key": "segreto"})])
        workflow_manager = FakeWorkflowManager({"pulizia": plan})
        plan_executor = FakePlanExecutor()
        policy_engine = PolicyEngine(blocked_intents={"RUN_COMMAND"}, always_confirm_intents={"FORGET"})
        skill = RunWorkflowSkill(workflow_manager, plan_executor, policy_engine=policy_engine)

        skill.execute({"name": "pulizia"})

        self.assertEqual(len(plan_executor.calls), 1)
        _, kwargs = plan_executor.calls[0]
        self.assertIs(kwargs["policy_engine"], policy_engine)

    def test_a_blocked_step_inside_a_saved_workflow_is_really_denied_end_to_end(self):
        """F1.2.5 ("applicare policy anche a... sotto-azioni generate da workflow"): il test
        sopra prova solo il CABLAGGIO (il policy_engine vero arriva a PlanExecutor.execute()),
        con un FakePlanExecutor che non applica alcuna policy per davvero. Qui si usa il
        PlanExecutor VERO (non un doppio) per provare che un'automazione con un passo bloccato
        viene DAVVERO fermata prima di toccare la skill - non solo che il parametro e' stato
        passato."""
        plan = Plan(steps=[PlanStep(intent="RUN_COMMAND", parameters={"command": "qualcosa"})])
        workflow_manager = FakeWorkflowManager({"automazione": plan})
        registry = _RecordingRegistry()
        real_plan_executor = PlanExecutor(registry)
        policy_engine = PolicyEngine(blocked_intents={"RUN_COMMAND"})
        skill = RunWorkflowSkill(workflow_manager, real_plan_executor, policy_engine=policy_engine)

        result = skill.execute({"name": "automazione"})

        self.assertTrue(result.success, "RunWorkflowSkill stessa riesce: e' il PASSO dentro il piano a fermarsi")
        outcome = result.data["outcome"]
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.stopped_step.result.error, "POLICY_BLOCKED")
        self.assertEqual(registry.executed_intents, [], "RUN_COMMAND non deve mai raggiungere la skill vera")

    def test_default_policy_engine_is_none_when_never_wired(self):
        """Chi costruisce la skill in isolamento senza collegare la policy (nessun JakeCore
        intorno) ottiene 'nessun controllo', non un crash - stesso comportamento di prima per
        chi non passa da JakeCore, ma esplicito, non un'omissione a runtime."""
        plan = Plan(steps=[PlanStep(intent="GET_TIME", parameters={})])
        workflow_manager = FakeWorkflowManager({"semplice": plan})
        plan_executor = FakePlanExecutor()
        skill = RunWorkflowSkill(workflow_manager, plan_executor)

        skill.execute({"name": "semplice"})

        _, kwargs = plan_executor.calls[0]
        self.assertIsNone(kwargs["policy_engine"])

    def test_dry_run_parameter_is_forwarded(self):
        plan = Plan(steps=[PlanStep(intent="GET_TIME", parameters={})])
        workflow_manager = FakeWorkflowManager({"semplice": plan})
        plan_executor = FakePlanExecutor()
        skill = RunWorkflowSkill(workflow_manager, plan_executor)

        skill.execute({"name": "semplice", "dry_run": True})

        _, kwargs = plan_executor.calls[0]
        self.assertTrue(kwargs["dry_run"])

    def test_dry_run_defaults_to_false(self):
        plan = Plan(steps=[PlanStep(intent="GET_TIME", parameters={})])
        workflow_manager = FakeWorkflowManager({"semplice": plan})
        plan_executor = FakePlanExecutor()
        skill = RunWorkflowSkill(workflow_manager, plan_executor)

        skill.execute({"name": "semplice"})

        _, kwargs = plan_executor.calls[0]
        self.assertFalse(kwargs["dry_run"])

    def test_successful_run_reports_the_outcome_and_step_count(self):
        sentinel_outcome = object()
        plan = Plan(steps=[PlanStep(intent="GET_TIME", parameters={})] * 3)
        workflow_manager = FakeWorkflowManager({"tre_passi": plan})
        plan_executor = FakePlanExecutor(outcome=sentinel_outcome)
        skill = RunWorkflowSkill(workflow_manager, plan_executor)

        result = skill.execute({"name": "tre_passi"})

        self.assertTrue(result.success)
        self.assertIs(result.data["outcome"], sentinel_outcome)
        self.assertEqual(result.data["total_steps"], 3)


class SaveWorkflowSkillTests(unittest.TestCase):
    def test_missing_name_or_request_is_reported(self):
        skill = SaveWorkflowSkill(FakePlannerProvider(), FakeWorkflowManager())

        self.assertEqual(skill.execute({"request": "fai qualcosa"}).error, "MISSING_PARAMETERS")
        self.assertEqual(skill.execute({"name": "x"}).error, "MISSING_PARAMETERS")

    def test_planner_failure_is_reported(self):
        skill = SaveWorkflowSkill(FakePlannerProvider(plan=None), FakeWorkflowManager())

        result = skill.execute({"name": "x", "request": "fai qualcosa"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "PLAN_FAILED")

    def test_successful_plan_is_saved_under_the_given_name(self):
        plan = Plan(steps=[PlanStep(intent="GET_TIME", parameters={})])
        workflow_manager = FakeWorkflowManager()
        skill = SaveWorkflowSkill(FakePlannerProvider(plan=plan), workflow_manager)

        result = skill.execute({"name": "buongiorno", "request": "dimmi l'ora"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["step_count"], 1)
        self.assertEqual(workflow_manager.saved, [("buongiorno", plan)])


if __name__ == "__main__":
    unittest.main()
