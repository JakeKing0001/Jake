"""Test unitari per skills/workflow.py (SAVE_WORKFLOW/RUN_WORKFLOW). Il modulo non aveva
ancora nessuna suite dedicata - esattamente come e' potuto restare inosservato il buco reale
verificato qui sotto (F1, vedi ROADMAP.md): RunWorkflowSkill non passava mai blocked_intents/
always_confirm_intents a PlanExecutor.execute(), che senza quei due argomenti non applica nessun
controllo (core/policy_engine.py, decide_automated). Un'automazione con un passo DESTRUCTIVE/
ADMIN non self-confirming (es. FORGET) eseguiva quel passo senza alcuna conferma, con un comando
diretto dell'utente - non serviva nemmeno un prompt costruito ad arte."""
import unittest

from core.planner import Plan, PlanStep
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

    def test_forwards_blocked_and_always_confirm_intents_to_the_executor(self):
        """Il buco reale: senza questo, un'automazione con un passo DESTRUCTIVE/ADMIN non
        self-confirming (es. FORGET) eseguiva senza alcuna conferma."""
        plan = Plan(steps=[PlanStep(intent="FORGET", parameters={"key": "segreto"})])
        workflow_manager = FakeWorkflowManager({"pulizia": plan})
        plan_executor = FakePlanExecutor()
        skill = RunWorkflowSkill(
            workflow_manager, plan_executor,
            blocked_intents={"RUN_COMMAND"}, always_confirm_intents={"FORGET"},
        )

        skill.execute({"name": "pulizia"})

        self.assertEqual(len(plan_executor.calls), 1)
        _, kwargs = plan_executor.calls[0]
        self.assertEqual(kwargs["blocked_intents"], {"RUN_COMMAND"})
        self.assertEqual(kwargs["always_confirm_intents"], {"FORGET"})

    def test_default_policy_sets_are_none_when_never_wired(self):
        """Chi costruisce la skill in isolamento senza collegare le policy (nessun JakeCore
        intorno) ottiene 'nessun controllo', non un crash - stesso comportamento di prima per
        chi non passa da JakeCore, ma esplicito, non un'omissione a runtime."""
        plan = Plan(steps=[PlanStep(intent="GET_TIME", parameters={})])
        workflow_manager = FakeWorkflowManager({"semplice": plan})
        plan_executor = FakePlanExecutor()
        skill = RunWorkflowSkill(workflow_manager, plan_executor)

        skill.execute({"name": "semplice"})

        _, kwargs = plan_executor.calls[0]
        self.assertIsNone(kwargs["blocked_intents"])
        self.assertIsNone(kwargs["always_confirm_intents"])

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
