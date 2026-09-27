"""F6.3.4: controllo dell'utente sulle notifiche proattive, sempre riferito all'ULTIMA notifica mostrata
(JakeCore.last_notification): "meno notifiche cosi'", "non mostrarmelo piu'", "mostramelo di nuovo", "rimandala di
N minuti". Agiscono sul TIPO di notifica (core/proactive_gate.py::notification_key). I promemoria non si silenziano
da qui: l'orario l'ha chiesto l'utente, per toglierli c'e' la cancellazione del promemoria."""
import time

from core.skill_result import SkillResult


def _last(core):
    last = getattr(core, "last_notification", None)
    if not last:
        return None, SkillResult(success=False, data={}, error="NO_RECENT_NOTIFICATION")
    return last, None


class LessNotificationsLikeThisSkill:
    metadata = {
        "intent": "LESS_NOTIFICATIONS_LIKE_THIS",
        "description": "Riduce le notifiche proattive del tipo dell'ultima mostrata: finiscono nel riepilogo invece di interrompere (torna normale col tempo).",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        last, error = _last(self.core)
        if error:
            return error
        if last["kind"] == "reminder":
            return SkillResult(success=False, data={}, error="REMINDER_NOT_MUTABLE")
        multiplier = self.core.notification_policy.feedback.less_like_this(last["key"])
        return SkillResult(success=True, data={"message": last["message"], "multiplier": round(multiplier, 2)})


class MuteNotificationSkill:
    metadata = {
        "intent": "MUTE_NOTIFICATION",
        "description": "Non mostra piu' le notifiche proattive del tipo dell'ultima mostrata (si riattiva con 'mostramelo di nuovo').",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        last, error = _last(self.core)
        if error:
            return error
        if last["kind"] == "reminder":
            return SkillResult(success=False, data={}, error="REMINDER_NOT_MUTABLE")
        self.core.notification_policy.feedback.mute(last["key"])
        return SkillResult(success=True, data={"message": last["message"]})


class UnmuteNotificationSkill:
    metadata = {
        "intent": "UNMUTE_NOTIFICATION",
        "description": "Riattiva le notifiche del tipo dell'ultima silenziata o ridotta.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        last, error = _last(self.core)
        if error:
            return error
        restored = self.core.notification_policy.feedback.undo(last["key"])
        return SkillResult(success=restored, data={"message": last["message"]}, error=None if restored else "NOTHING_TO_RESTORE")


class SnoozeNotificationSkill:
    metadata = {
        "intent": "SNOOZE_NOTIFICATION",
        "description": "Rimanda di qualche minuto l'ultima notifica proattiva: torna nel riepilogo non prima di allora.",
        "parameters": {
            "minutes": {"type": "integer", "required": False, "description": "Dopo quanti minuti riproporla (default 60)."},
        },
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        last, error = _last(self.core)
        if error:
            return error
        try:
            minutes = max(1, min(24 * 60, int((parameters or {}).get("minutes") or 60)))
        except (TypeError, ValueError):
            return SkillResult(success=False, data={}, error="INVALID_PARAMETERS")
        self.core.notification_center.defer(last["kind"], last["message"], not_before=time.time() + minutes * 60)
        return SkillResult(success=True, data={"message": last["message"], "minutes": minutes})
