from datetime import datetime, timedelta, timezone

from core.skill_result import SkillResult


class SetReminderSkill:
    metadata = {
        "intent": "SET_REMINDER",
        "description": "Imposta un promemoria che Jake segnalera' al momento giusto.",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Cosa ricordare, con le stesse parole dell'utente.",
            },
            "in_minutes": {
                "type": "integer",
                "required": False,
                "description": "Tra quanti minuti da ora avvisare. Usalo se l'utente dice 'tra X minuti/ore'.",
            },
            "at_time": {
                "type": "string",
                "required": False,
                "description": "Orario assoluto 'HH:MM' (24 ore) in cui avvisare oggi (domani se gia' passato).",
            },
        },
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        in_minutes = parameters.get("in_minutes")
        at_time = (parameters.get("at_time") or "").strip()

        if not text or (in_minutes is None and not at_time):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        now_local = datetime.now().astimezone()
        if in_minutes is not None:
            due_local = now_local + timedelta(minutes=int(in_minutes))
        else:
            try:
                hour, minute = (int(part) for part in at_time.split(":"))
            except ValueError:
                return SkillResult(success=False, data={"at_time": at_time}, error="INVALID_TIME")
            due_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due_local <= now_local:
                due_local += timedelta(days=1)

        due_utc = due_local.astimezone(timezone.utc)
        self.reminder_manager.add(text, due_utc)
        return SkillResult(success=True, data={"text": text, "due_at_local": due_local.strftime("%H:%M")})


class ListRemindersSkill:
    metadata = {
        "intent": "LIST_REMINDERS",
        "description": "Elenca i promemoria non ancora scaduti.",
        "parameters": {},
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        reminders = self.reminder_manager.list_upcoming()
        if not reminders:
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        local_reminders = []
        for reminder in reminders:
            due_local = datetime.fromisoformat(reminder["due_at"]).astimezone()
            local_reminders.append({"text": reminder["text"], "due_at_local": due_local.strftime("%d/%m %H:%M")})
        return SkillResult(success=True, data={"reminders": local_reminders})
