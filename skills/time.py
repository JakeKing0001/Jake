import datetime

from core.skill_result import SkillResult


class TimeSkill:
    metadata = {
        "intent": "GET_TIME",
        "description": "Restituisce l'ora corrente.",
        "parameters": {},
    }

    def __init__(self):
        pass

    def execute(self, parameters: dict = None):
        """Restituisce l'ora attuale. Ignora i parametri."""
        current_time = datetime.datetime.now().time().replace(microsecond=0)
        return SkillResult(success=True, data={"time": str(current_time)})
    