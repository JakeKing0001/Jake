from core.notification_center import NotificationMode
from core.skill_result import SkillResult


class SetNotificationModeSkill:
    metadata = {
        "intent": "SET_NOTIFICATION_MODE",
        "description": "Imposta la modalita' di notifica di Jake (normale, non disturbare, gioco, studio, "
        "riunione, sonno): in modalita' diverse da 'normale' gli avvisi proattivi e le automazioni non "
        "interrompono piu' subito, restano in coda. Usalo per 'metti la modalita' non disturbare', "
        "'sto giocando', 'sono in riunione', 'torna normale'.",
        "parameters": {
            "mode": {
                "type": "string", "required": True,
                "description": "Una tra: normal, do_not_disturb, gaming, study, meeting, sleep.",
            },
        },
    }

    def __init__(self, notification_center):
        self.notification_center = notification_center

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw = (parameters.get("mode") or "").strip().lower()
        try:
            mode = NotificationMode(raw)
        except ValueError:
            return SkillResult(success=False, data={"mode": raw}, error="INVALID_VALUE")

        released = self.notification_center.set_mode(mode)
        return SkillResult(success=True, data={"mode": mode.value, "released": released})


class GetNotificationModeSkill:
    metadata = {
        "intent": "GET_NOTIFICATION_MODE",
        "description": "Dice in che modalita' di notifica si trova Jake in questo momento.",
        "parameters": {},
    }

    def __init__(self, notification_center):
        self.notification_center = notification_center

    def execute(self, parameters: dict = None):
        return SkillResult(success=True, data={
            "mode": self.notification_center.mode.value,
            "pending": self.notification_center.pending_count(),
        })
