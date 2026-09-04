from core.skill_result import SkillResult


class GetActiveWindowSkill:
    metadata = {
        "intent": "GET_ACTIVE_WINDOW",
        "description": "Restituisce il titolo della finestra attualmente in primo piano (cosa sta facendo l'utente).",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        from core.vision.screen import get_active_window_title

        try:
            title = get_active_window_title()
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        if not title:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"title": title})
