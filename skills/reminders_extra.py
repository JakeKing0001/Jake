from datetime import datetime, timedelta, timezone

from core.skill_result import SkillResult


class SnoozeReminderSkill:
    metadata = {
        "intent": "SNOOZE_REMINDER",
        "description": "Rinvia un promemoria gia' impostato di qualche minuto, cercandolo per somiglianza col testo.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Testo (anche parziale) del promemoria da rinviare."},
            "minutes": {"type": "integer", "required": True, "description": "Tra quanti minuti da ora ripresentarlo."},
        },
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        minutes = parameters.get("minutes")
        if not text or not isinstance(minutes, int):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        snoozed = self.reminder_manager.snooze_matching(text, minutes)
        if snoozed is None:
            return SkillResult(success=False, data={"text": text}, error="NOT_FOUND")
        return SkillResult(success=True, data={"text": snoozed["text"], "minutes": minutes})


class DeleteReminderSkill:
    metadata = {
        "intent": "DELETE_REMINDER",
        "description": "Cancella un promemoria gia' impostato, cercandolo per somiglianza col testo. "
        "Diverso da FORGET, che riguarda i ricordi chiave-valore, non i promemoria a tempo.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Testo (anche parziale) del promemoria da cancellare."},
        },
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        deleted = self.reminder_manager.delete_matching(text)
        if deleted is None:
            return SkillResult(success=False, data={"text": text}, error="NOT_FOUND")
        return SkillResult(success=True, data={"text": deleted["text"]})


class SetDailyReminderSkill:
    metadata = {
        "intent": "SET_DAILY_REMINDER",
        "description": "Imposta un promemoria che si ripete ogni giorno alla stessa ora. Diverso da "
        "SET_REMINDER, che avvisa una sola volta.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Cosa ricordare ogni giorno."},
            "at_time": {"type": "string", "required": True, "description": "Orario 'HH:MM' (24 ore) in cui avvisare ogni giorno."},
        },
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        at_time = (parameters.get("at_time") or "").strip()
        if not text or not at_time:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            hour, minute = (int(part) for part in at_time.split(":"))
        except ValueError:
            return SkillResult(success=False, data={"at_time": at_time}, error="INVALID_TIME")

        now_local = datetime.now().astimezone()
        due_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due_local <= now_local:
            due_local += timedelta(days=1)

        due_utc = due_local.astimezone(timezone.utc)
        self.reminder_manager.add(text, due_utc, recur_time=at_time)
        return SkillResult(success=True, data={"text": text, "at_time": at_time})


class StartPomodoroSkill:
    """Imposta un promemoria di pausa tra N minuti, taggato in modo riconoscibile ('sessione
    pomodoro') cosi' STOP_POMODORO puo' trovarlo e cancellarlo senza bisogno di uno stato a parte."""

    metadata = {
        "intent": "START_POMODORO",
        "description": "Avvia una sessione di lavoro a tempo (tecnica del pomodoro): Jake avvisera' allo scadere.",
        "parameters": {
            "minutes": {"type": "integer", "required": False, "description": "Durata della sessione in minuti (default 25)."},
        },
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        minutes = parameters.get("minutes") or 25
        due_utc = datetime.now(timezone.utc) + timedelta(minutes=int(minutes))
        self.reminder_manager.add("fine sessione pomodoro, fai una pausa", due_utc)
        return SkillResult(success=True, data={"minutes": minutes})


class StopPomodoroSkill:
    metadata = {
        "intent": "STOP_POMODORO",
        "description": "Ferma la sessione pomodoro in corso, annullando l'avviso di fine sessione.",
        "parameters": {},
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        deleted = self.reminder_manager.delete_matching("pomodoro")
        if deleted is None:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={})
