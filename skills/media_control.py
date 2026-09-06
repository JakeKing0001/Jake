from core.skill_result import SkillResult


class MediaControlSkill:
    """Controlla la riproduzione multimediale simulando i tasti multimediali di sistema (stesso
    approccio di VolumeControlSkill): funziona con qualunque player attivo (Spotify, YouTube,
    Windows Media Player...), non serve integrare le singole app."""

    metadata = {
        "intent": "MEDIA_CONTROL",
        "description": "Controlla la riproduzione multimediale in corso (musica/video): play/pausa, "
        "traccia successiva, traccia precedente.",
        "parameters": {
            "action": {
                "type": "string",
                "required": True,
                "description": "Una tra: 'play_pause', 'next', 'previous'.",
            },
        },
    }

    KEY_BY_ACTION = {
        "play_pause": "play/pause media",
        "next": "next track",
        "previous": "previous track",
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
