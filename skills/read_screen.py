from core.skill_result import SkillResult


class ReadScreenSkill:
    """Legge il testo visibile sullo schermo via OCR (v1.3, multimodalita').

    Non e' comprensione visiva vera e propria (Jake non ha un modello con visione collegato):
    estrae solo il testo presente sullo schermo, non descrive immagini o elementi grafici."""

    metadata = {
        "intent": "READ_SCREEN",
        "description": "Legge (via OCR) il testo attualmente visibile sullo schermo.",
        "parameters": {},
    }

    MAX_CHARS = 800

    def execute(self, parameters: dict = None):
        from core.vision.screen import read_screen_text

        try:
            text = read_screen_text()
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        if text is None:
            return SkillResult(success=False, data={}, error="OCR_UNAVAILABLE")
        text = text.strip()
        if not text:
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        truncated = len(text) > self.MAX_CHARS
        return SkillResult(success=True, data={"text": text[:self.MAX_CHARS], "truncated": truncated})
