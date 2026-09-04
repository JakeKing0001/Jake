from core.skill_result import SkillResult


class DescribeScreenSkill:
    """Descrive cosa c'e' sullo schermo con un modello di visione locale (v3.0), a
    complemento di READ_SCREEN che estrae solo il testo via OCR."""

    metadata = {
        "intent": "DESCRIBE_SCREEN",
        "description": "Descrive cosa si vede sullo schermo: layout, immagini, grafici, non solo il testo.",
        "parameters": {
            "question": {
                "type": "string",
                "required": False,
                "description": "Cosa chiedere in particolare sull'immagine (es. 'cosa mostra questo grafico'). "
                "Se assente, ne da' una descrizione generale.",
            },
        },
    }

    def __init__(self, vision_provider):
        self.vision_provider = vision_provider

    def execute(self, parameters: dict = None):
        from core.vision.screen import capture_screenshot

        parameters = parameters or {}
        question = (parameters.get("question") or "").strip() or None

        try:
            path = capture_screenshot()
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        description = self.vision_provider.describe(path, question)
        if description is None:
            return SkillResult(success=False, data={}, error="VISION_UNAVAILABLE")

        return SkillResult(success=True, data={"description": description})
