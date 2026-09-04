from core.skill_result import SkillResult


class ForgetSkill:
    metadata = {
        "intent": "FORGET",
        "description": "Elimina un'informazione precedentemente memorizzata.",
        "parameters": {
            "key": {
                "type": "string",
                "required": True,
                "description": "Nome esatto dell'informazione da eliminare.",
            },
        },
    }

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        key = (parameters.get("key") or "").strip()

        if not key:
            return SkillResult(success=False, data={"key": key}, error="MISSING_PARAMETERS")

        deleted = self.memory_manager.forget(key)
        if not deleted:
            return SkillResult(success=False, data={"key": key}, error="NOT_FOUND")
        return SkillResult(success=True, data={"key": key})
