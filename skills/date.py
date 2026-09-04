import datetime

from core.skill_result import SkillResult


class DateSkill:
    metadata = {
        "intent": "GET_DATE",
        "description": "Restituisce la data corrente.",
        "parameters": {},
    }

    def __init__(self):
        pass

    def execute(self, parameters: dict = None):
        """Restituisce la data attuale. Ignora i parametri."""
        date = datetime.datetime.now().date().strftime("%d/%m/%Y")
        return SkillResult(success=True, data={"date": date})
    