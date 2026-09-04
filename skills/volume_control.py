from core.skill_result import SkillResult


class VolumeControlSkill:
    """Regola il volume di sistema simulando i tasti multimediali (nessuna libreria audio dedicata)."""

    metadata = {
        "intent": "SET_VOLUME",
        "description": "Alza, abbassa o silenzia il volume di sistema.",
        "parameters": {
            "action": {
                "type": "string",
                "required": True,
                "description": "Una tra: 'up', 'down', 'mute'.",
            },
        },
    }

    KEY_BY_ACTION = {
        "up": "volume up",
        "down": "volume down",
        "mute": "volume mute",
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        action = (parameters.get("action") or "").strip().lower()
        key = self.KEY_BY_ACTION.get(action)
        if key is None:
            return SkillResult(success=False, data={"action": action}, error="MISSING_PARAMETERS")

        try:
            import keyboard
            keyboard.send(key)
        except Exception:
            return SkillResult(success=False, data={"action": action}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"action": action})
