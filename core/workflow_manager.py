import json

from core.planner import Plan, PlanStep


class WorkflowManager:
    """Salva e richiama sequenze di passi con nome: le 'automazioni configurabili' di Jake.

    Un workflow e' semplicemente un Plan (v0.7) salvato con un nome, persistito nella stessa
    memoria a lungo termine usata da REMEMBER/RECALL (categoria dedicata 'workflow')."""

    CATEGORY = "workflow"

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    def save(self, name: str, plan: Plan) -> None:
        steps_data = [
            {"intent": step.intent, "parameters": step.parameters, "description": step.description}
            for step in plan.steps
        ]
        self.memory_manager.remember(name, json.dumps(steps_data), category=self.CATEGORY)

    def load(self, name: str) -> Plan | None:
        results = self.memory_manager.recall(key=name, category=self.CATEGORY, limit=1)
        if not results:
            return None
        steps_data = json.loads(results[0]["value"])
        steps = [PlanStep(**step) for step in steps_data]
        return Plan(steps=steps)

    def list_names(self) -> list[str]:
        results = self.memory_manager.recall(category=self.CATEGORY, limit=50)
        return [result["key"] for result in results]
