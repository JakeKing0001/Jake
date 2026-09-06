import subprocess
import sys

from core.skill_result import SkillResult


class SystemPowerSkill:
    """Spegnimento/riavvio/sospensione/blocco del PC. Spegnimento e riavvio richiedono sempre
    conferma esplicita (come CLOSE_APP): sono azioni irreversibili con impatto su tutto il PC,
    non solo su un'applicazione."""

    metadata = {
        "intent": "SYSTEM_POWER",
        "description": "Spegne, riavvia, sospende o blocca il computer.",
        "parameters": {
            "action": {
                "type": "string",
                "required": True,
                "description": "Una tra: 'shutdown' (spegni), 'restart' (riavvia), 'sleep' "
                "(sospendi/standby), 'lock' (blocca lo schermo).",
            },
        },
    }

    CONFIRM_REQUIRED_ACTIONS = {"shutdown", "restart"}

    ACTION_LABELS = {
        "shutdown": "spegnere il computer",
        "restart": "riavviare il computer",
        "sleep": "mettere in sospensione il computer",
        "lock": "bloccare lo schermo",
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        action = (parameters.get("action") or "").strip().lower()
        if action not in self.ACTION_LABELS:
            return SkillResult(success=False, data={"action": action}, error="MISSING_PARAMETERS")

        if action in self.CONFIRM_REQUIRED_ACTIONS and not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "action": action,
                    "message": f"Confermi di voler {self.ACTION_LABELS[action]}?",
                    "confirm_parameters": {"action": action, "confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        if sys.platform != "win32":
            return SkillResult(success=False, data={"action": action}, error="OPERATION_FAILED")

        try:
            if action == "shutdown":
                subprocess.run(["shutdown", "/s", "/t", "5"], check=True)
            elif action == "restart":
                subprocess.run(["shutdown", "/r", "/t", "5"], check=True)
            elif action == "sleep":
                subprocess.run(
                    ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=True
                )
            elif action == "lock":
                subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=True)
        except Exception:
            return SkillResult(success=False, data={"action": action}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"action": action})
