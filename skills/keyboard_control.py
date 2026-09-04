from core.skill_result import SkillResult


class TypeTextSkill:
    metadata = {
        "intent": "TYPE_TEXT",
        "description": "Digita del testo nel punto in cui si trova il focus della tastiera (es. un campo di testo aperto).",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Testo da digitare, con le stesse parole dell'utente.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        import pyautogui

        parameters = parameters or {}
        text = parameters.get("text") or ""
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            pyautogui.write(text, interval=0.01)
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"text": text})


class PressKeySkill:
    metadata = {
        "intent": "PRESS_KEY",
        "description": "Preme un tasto o una combinazione di tasti (es. 'enter', 'ctrl+c', 'alt+tab').",
        "parameters": {
            "keys": {
                "type": "string",
                "required": True,
                "description": "Tasto o combinazione separata da '+', es. 'ctrl+s' o 'enter'.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        import pyautogui

        parameters = parameters or {}
        keys = (parameters.get("keys") or "").strip().lower()
        if not keys:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        key_sequence = [key.strip() for key in keys.split("+") if key.strip()]
        try:
            if len(key_sequence) > 1:
                pyautogui.hotkey(*key_sequence)
            else:
                pyautogui.press(key_sequence[0])
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"keys": keys})
