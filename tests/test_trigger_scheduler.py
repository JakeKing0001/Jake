"""Test unitari per core/trigger_scheduler.py::TriggerScheduler._fire(), in particolare il
budget di autonomia (F6, Proactive Intelligence & Autonomy - vedi core/autonomy_budget.py e
ROADMAP.md). Il modulo non aveva ancora una suite dedicata. Usa fake/mock per trigger_manager/
workflow_manager/plan_executor: _fire() non fa nulla di verificabile su disco da solo, la sua
logica e' tutta nell'orchestrare quelle tre collaborazioni."""
import time
import unittest
from unittest import mock

from core.autonomy_budget import AutonomyBudget
from core.planner import Plan, PlanStep
from core.policy_engine import PolicyEngine
from core.trigger_scheduler import TriggerScheduler


class FireTests(unittest.TestCase):
    def _scheduler(self, autonomy_budget=None, plan=None, policy_engine=None):
        self.trigger_manager = mock.Mock()
        self.workflow_manager = mock.Mock()
        self.plan_executor = mock.Mock()
        self.workflow_manager.load.return_value = plan if plan is not None else Plan(
            steps=[PlanStep(intent="GET_TIME", parameters={})]
        )
        self.plan_executor.execute.return_value = mock.Mock()
        self.on_trigger = mock.Mock()
        return TriggerScheduler(
            self.trigger_manager, self.workflow_manager, self.plan_executor, None,
            on_trigger=self.on_trigger, autonomy_budget=autonomy_budget, policy_engine=policy_engine,
        )

    def test_fire_forwards_the_policy_engine_to_the_executor(self):
        """F1 (core/policy_engine.py): stesso principio di RunWorkflowSkillTests - un riferimento
        solo da collegare, non piu' due insiemi separati che si potevano dimenticare a meta'."""
        policy_engine = PolicyEngine(blocked_intents={"RUN_COMMAND"})
        scheduler = self._scheduler(policy_engine=policy_engine)

        scheduler._fire({"name": "buonanotte", "workflow_name": "buonanotte"})

        _, kwargs = self.plan_executor.execute.call_args
        self.assertIs(kwargs["policy_engine"], policy_engine)

    def test_fire_executes_the_plan_and_marks_fired_when_budget_allows(self):
        scheduler = self._scheduler()

        scheduler._fire({"name": "buonanotte", "workflow_name": "buonanotte"})

        self.plan_executor.execute.assert_called_once()
        self.trigger_manager.mark_fired.assert_called_once()
        self.on_trigger.assert_called_once()

    def test_fire_does_nothing_when_budget_is_exhausted(self):
        """F6: il caso che il budget deve prevenire - un trigger che continuerebbe a far
        partire automazioni senza limite."""
        budget = AutonomyBudget(max_actions=1)
        budget.record()  # esaurisce subito il budget
        scheduler = self._scheduler(autonomy_budget=budget)

        scheduler._fire({"name": "buonanotte", "workflow_name": "buonanotte"})

        self.plan_executor.execute.assert_not_called()
        self.trigger_manager.mark_fired.assert_not_called()
        self.on_trigger.assert_not_called()

    def test_fire_records_budget_usage_only_when_it_actually_runs(self):
        budget = AutonomyBudget(max_actions=5)
        scheduler = self._scheduler(autonomy_budget=budget)

        scheduler._fire({"name": "buonanotte", "workflow_name": "buonanotte"})

        self.assertEqual(budget.remaining(), 4)

    def test_fire_does_not_consume_budget_when_the_workflow_no_longer_exists(self):
        """Un'automazione cancellata nel frattempo non deve costare un posto nel budget: non e'
        mai partita per davvero."""
        budget = AutonomyBudget(max_actions=5)
        scheduler = self._scheduler(autonomy_budget=budget, plan=None)
        self.workflow_manager.load.return_value = None

        scheduler._fire({"name": "buonanotte", "workflow_name": "sparito"})

        self.assertEqual(budget.remaining(), 5)
        self.plan_executor.execute.assert_not_called()

    def test_default_autonomy_budget_is_created_when_none_is_passed(self):
        scheduler = self._scheduler(autonomy_budget=None)

        self.assertIsInstance(scheduler.autonomy_budget, AutonomyBudget)


class StopTimeoutTests(unittest.TestCase):
    """F1.8.5 ("aggiungere deadlock timeout e diagnosi"): buco reale, riprodotto per davvero -
    se _run() era bloccato (qui in list_all()) oltre i 2s di timeout di stop(), join() tornava
    comunque, silenziosamente, senza dire che il thread era ANCORA vivo."""

    def test_stop_logs_a_warning_when_the_thread_does_not_stop_in_time(self):
        trigger_manager = mock.Mock()
        trigger_manager.list_all.side_effect = lambda: time.sleep(0.5)
        scheduler = TriggerScheduler(
            trigger_manager, mock.Mock(), mock.Mock(), None, interval_seconds=100, stop_timeout_seconds=0.05,
        )
        scheduler._logger = mock.Mock()
        scheduler.start()
        self.addCleanup(lambda: scheduler._thread.join(timeout=5))
        time.sleep(0.05)  # lascia partire _run() e bloccarsi dentro list_all()

        scheduler.stop()

        self.assertTrue(scheduler._thread.is_alive(), "il thread deve essere ancora bloccato a questo punto")
        scheduler._logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
