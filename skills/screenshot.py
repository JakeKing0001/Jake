from core.skill_result import SkillResult


class TakeScreenshotSkill:
    metadata = {
        "intent": "TAKE_SCREENSHOT",
        "description": "Cattura uno screenshot dello schermo e lo salva su disco.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        from core.vision.screen import capture_screenshot

        try:
            path = capture_screenshot()
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"path": str(path)})
