from core.skill_result import SkillResult


class ClipboardReadSkill:
    metadata = {
        "intent": "CLIPBOARD_READ",
        "description": "Legge il testo attualmente negli appunti.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            try:
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return SkillResult(success=False, data={}, error="CLIPBOARD_EMPTY")

        if not text:
            return SkillResult(success=False, data={}, error="CLIPBOARD_EMPTY")
        return SkillResult(success=True, data={"text": text})


class ClipboardWriteSkill:
    metadata = {
        "intent": "CLIPBOARD_WRITE",
        "description": "Scrive un testo negli appunti.",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Testo da copiare negli appunti.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = parameters.get("text") or ""
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return SkillResult(success=False, data={"text": text}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"text": text})
