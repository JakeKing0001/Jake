from core.skill_result import SkillResult


class ClickMouseSkill:
    """Sposta il mouse e clicca. Le coordinate sono in pixel dall'angolo in alto a sinistra.

    Sicurezza: pyautogui.FAILSAFE resta attivo (portare il mouse in un angolo dello schermo
    interrompe immediatamente qualunque automazione in corso)."""

    metadata = {
        "intent": "CLICK_MOUSE",
        "description": "Sposta il mouse in una posizione dello schermo e clicca.",
        "parameters": {
            "x": {"type": "integer", "required": True, "description": "Coordinata X in pixel."},
            "y": {"type": "integer", "required": True, "description": "Coordinata Y in pixel."},
            "button": {
                "type": "string",
                "required": False,
                "description": "'left' (default), 'right' o 'double' per doppio click.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        import pyautogui

        parameters = parameters or {}
        x = parameters.get("x")
        y = parameters.get("y")
        button = (parameters.get("button") or "left").strip().lower()
        if x is None or y is None:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            if button == "double":
                pyautogui.doubleClick(x=int(x), y=int(y))
            else:
                pyautogui.click(x=int(x), y=int(y), button="right" if button == "right" else "left")
        except pyautogui.FailSafeException:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"x": x, "y": y, "button": button})


class MoveMouseSkill:
    metadata = {
        "intent": "MOVE_MOUSE",
        "description": "Sposta il puntatore del mouse in una posizione dello schermo, senza cliccare.",
        "parameters": {
            "x": {"type": "integer", "required": True, "description": "Coordinata X in pixel."},
            "y": {"type": "integer", "required": True, "description": "Coordinata Y in pixel."},
        },
    }

    def execute(self, parameters: dict = None):
        import pyautogui

        parameters = parameters or {}
        x = parameters.get("x")
        y = parameters.get("y")
        if x is None or y is None:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            pyautogui.moveTo(int(x), int(y))
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"x": x, "y": y})
