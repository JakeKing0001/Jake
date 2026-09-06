import subprocess

from core.skill_result import SkillResult


class SetBrightnessSkill:
    """Regola la luminosita' dello schermo via WMI (nessuna libreria esterna): funziona sui
    pannelli che espongono WmiMonitorBrightnessMethods (tipicamente laptop), non sempre sui
    monitor esterni da desktop."""

    metadata = {
        "intent": "SET_BRIGHTNESS",
        "description": "Imposta la luminosita' dello schermo a una percentuale specifica.",
        "parameters": {
            "level": {
                "type": "integer",
                "required": True,
                "description": "Percentuale di luminosita' da 0 a 100.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        level = parameters.get("level")
        if not isinstance(level, int):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        level = max(0, min(100, level))

        command = (
            "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1,{level})"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                check=True, capture_output=True, timeout=10,
            )
        except Exception:
            return SkillResult(success=False, data={"level": level}, error="BRIGHTNESS_UNAVAILABLE")

        return SkillResult(success=True, data={"level": level})
