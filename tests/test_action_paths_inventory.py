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
        # documento: policy_engine=None equivale a "nessun controllo").
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

    def test_raw_dispatch_path_has_no_policy_parameter(self):
        """Documenta il buco noto (F1.2.1): SkillRegistry.execute() non riceve/controlla un
        policy_engine, a differenza di PlanExecutor.execute e rollback_effect qui sopra. Se in
        futuro venisse aggiunto un controllo di policy qui, questo test fallira' e andra'
        aggiornato insieme al documento (non e' un requisito che debba MAI accadere, solo una
        prova che oggi non accade ancora - vedi docs/action-execution-paths.md)."""
        params = inspect.signature(core.skill_registry.SkillRegistry.execute).parameters
        self.assertNotIn("policy_engine", params)


if __name__ == "__main__":
    unittest.main()
