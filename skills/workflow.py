from core.skill_result import SkillResult


class SaveWorkflowSkill:
    """Scompone una richiesta in passi (via planner) e li salva con un nome riutilizzabile."""

    metadata = {
        "intent": "SAVE_WORKFLOW",
        "description": "Salva una sequenza di passi con un nome, da poter rieseguire in seguito.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome con cui richiamare in futuro questa automazione.",
            },
            "request": {
                "type": "string",
                "required": True,
                "description": "Descrizione di cosa deve fare l'automazione, con le stesse parole dell'utente.",
            },
        },
    }

    def __init__(self, planner_provider, workflow_manager):
        self.planner_provider = planner_provider
        self.workflow_manager = workflow_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        request = (parameters.get("request") or "").strip()
        if not name or not request:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        plan = self.planner_provider.build_plan(request)
        if plan is None or not plan.steps:
            return SkillResult(success=False, data={"name": name}, error="PLAN_FAILED")

        self.workflow_manager.save(name, plan)
        return SkillResult(success=True, data={"name": name, "step_count": len(plan.steps)})


class RunWorkflowSkill:
    """Riesegue un'automazione salvata in precedenza, passo per passo."""

    metadata = {
        "intent": "RUN_WORKFLOW",
        "description": "Esegue un'automazione precedentemente salvata, dato il suo nome.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome dell'automazione salvata da eseguire.",
            },
        },
    }

    def __init__(self, workflow_manager, plan_executor):
        self.workflow_manager = workflow_manager
        self.plan_executor = plan_executor

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        plan = self.workflow_manager.load(name)
        if plan is None:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")

        outcome = self.plan_executor.execute(plan)
        return SkillResult(success=True, data={"name": name, "outcome": outcome, "total_steps": len(plan.steps)})
