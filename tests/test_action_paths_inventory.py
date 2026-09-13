"""F1.1.1 (Action Contract 2.0): docs/action-execution-paths.md elenca ogni percorso con cui un
intent puo' arrivare a eseguire davvero. Questo file non ri-verifica il COMPORTAMENTO di ogni
percorso (gia' coperto altrove: tests/test_jake_core_permissions.py, tests/test_agent.py,
tests/test_plan_executor.py, tests/test_action_contract.py) - controlla solo che i simboli
citati nella tabella esistano ancora con quel nome, cosi' un rinominamento o una rimozione fa
fallire un test invece di lasciare l'inventario silenziosamente non aggiornato."""
import inspect
import unittest

import core.agent
import core.companion_server
import core.execution_safety
import core.jake_core
import core.plan_executor
import core.plugin_loader
import core.skill_forge
import core.skill_registry
import core.trigger_scheduler
import skills.workflow


class DocumentedSymbolsStillExistTests(unittest.TestCase):
    def test_direct_command_path(self):
        self.assertTrue(hasattr(core.jake_core.JakeCore, "_resolve_and_execute"))
        self.assertTrue(hasattr(core.jake_core.JakeCore, "_authorize_command"))
        self.assertTrue(hasattr(core.jake_core.JakeCore, "_finalize_pending_action"))
        self.assertTrue(hasattr(core.jake_core.JakeCore, "_log_action_outcome"))
        self.assertTrue(hasattr(core.jake_core.JakeCore, "_log_denied_action"))

    def test_task_agent_path(self):
        self.assertTrue(hasattr(core.agent.TaskAgent, "run"))
        self.assertTrue(hasattr(core.agent.TaskAgent, "_log_step"))
        self.assertTrue(hasattr(core.agent.TaskAgent, "_rollback"))
        # L'agente a passi esegue tramite un executor iniettato (di solito
        # JakeCore._resolve_and_execute), non con la propria logica di policy.
        params = inspect.signature(core.agent.TaskAgent.__init__).parameters
        self.assertIn("executor", params)
        self.assertIn("policy_engine", params)

    def test_automated_plan_path(self):
        self.assertTrue(hasattr(core.plan_executor.PlanExecutor, "execute"))
        self.assertTrue(hasattr(core.plan_executor.PlanExecutor, "_log_step"))
        self.assertTrue(hasattr(core.plan_executor.PlanExecutor, "_rollback"))
        params = inspect.signature(core.plan_executor.PlanExecutor.execute).parameters
        self.assertIn("policy_engine", params)
        # I tre chiamanti reali che devono passare il proprio policy_engine (vedi la nota nel
        # documento: policy_engine=None ora e' fail-closed).
        self.assertTrue(hasattr(core.jake_core.JakeCore, "_try_plan"))
        self.assertTrue(hasattr(skills.workflow, "RunWorkflowSkill"))
        self.assertTrue(hasattr(core.trigger_scheduler.TriggerScheduler, "_fire"))

    def test_companion_server_path(self):
        self.assertTrue(hasattr(core.companion_server, "CompanionServer"))
        self.assertTrue(hasattr(core.companion_server._Handler, "_handle_command"))

    def test_skill_registration_paths(self):
        self.assertTrue(hasattr(core.plugin_loader, "load_plugins") or hasattr(core.plugin_loader, "PluginLoader"))
        self.assertTrue(hasattr(core.skill_forge, "SkillForge"))
        self.assertTrue(hasattr(core.skill_registry.SkillRegistry, "register_skill"))

    def test_rollback_path(self):
        self.assertTrue(hasattr(core.execution_safety, "rollback_effect"))
        params = inspect.signature(core.execution_safety.rollback_effect).parameters
        self.assertIn("policy_engine", params)

    def test_raw_dispatch_path_is_now_fail_closed_too(self):
        """F1.2.1 (percorso 7, l'ultimo dei tre "percorso N" dichiarati aperti - chiude il gap che
        questo stesso test documentava fino a poco fa): SkillRegistry.execute() ora riceve/
        controlla un policy_engine, con lo stesso principio fail-closed gia' applicato a
        PlanExecutor.execute e rollback_effect qui sopra - vedi il docstring di
        SkillRegistry.execute() e docs/action-execution-paths.md ("Nota sul percorso 7")."""
        params = inspect.signature(core.skill_registry.SkillRegistry.execute).parameters
        self.assertIn("policy_engine", params)


if __name__ == "__main__":
    unittest.main()
