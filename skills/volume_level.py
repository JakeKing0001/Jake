"""Volume esatto (v3.0) via pycaw/CoreAudio: "volume al 30", "a quanto e' il volume".
SET_VOLUME (v0.9) simula i tasti multimediali: comodo per 'alza/abbassa', ma non puo'
impostare un livello preciso."""
from core.skill_result import SkillResult


def _endpoint_volume():
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    device = AudioUtilities.GetSpeakers()
    endpoint = getattr(device, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint
    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


class SetVolumeLevelSkill:
    metadata = {
        "intent": "SET_VOLUME_LEVEL",
        "description": "Imposta il volume di sistema a una percentuale precisa (0-100). Usalo per "
        "'volume al 50', 'metti il volume a 20', 'volume al massimo/minimo'. Diverso da SET_VOLUME "
        "(alza/abbassa/silenzia di un passo).",
        "parameters": {
            "level": {"type": "integer", "required": True, "description": "Percentuale da 0 (minimo) a 100 (massimo)."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        level = parameters.get("level")
        try:
            level = int(level)
        except (TypeError, ValueError):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        level = max(0, min(100, level))
        try:
            volume = _endpoint_volume()
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            if level > 0 and volume.GetMute():
                volume.SetMute(0, None)
        except Exception:
            return SkillResult(success=False, data={"level": level}, error="AUDIO_UNAVAILABLE")
        return SkillResult(success=True, data={"level": level})


class GetVolumeLevelSkill:
    metadata = {
        "intent": "GET_VOLUME_LEVEL",
        "description": "Dice a che percentuale e' il volume di sistema e se e' silenziato.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        try:
            volume = _endpoint_volume()
            level = round(volume.GetMasterVolumeLevelScalar() * 100)
            muted = bool(volume.GetMute())
        except Exception:
            return SkillResult(success=False, data={}, error="AUDIO_UNAVAILABLE")
        return SkillResult(success=True, data={"level": level, "muted": muted})
