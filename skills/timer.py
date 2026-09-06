"""Timer (v3.0): "metti un timer di 2 minuti", "annulla il timer", "quanto manca".

Un timer e' un promemoria di tipo 'timer' (stessa tabella, colonna kind): riusa lo scheduler
esistente, ma a scadenza Jake dice "e' scaduto il timer" invece di leggere un promemoria, e
"annulla il timer" trova SOLO i timer, non i promemoria dell'utente."""
from datetime import datetime, timedelta, timezone

from core.skill_result import SkillResult


def human_duration(total_seconds: int) -> str:
    total_seconds = int(total_seconds)
    hours, rest = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = []
    if hours:
        parts.append("1 ora" if hours == 1 else f"{hours} ore")
    if minutes:
        parts.append("1 minuto" if minutes == 1 else f"{minutes} minuti")
    if seconds or not parts:
        parts.append("1 secondo" if seconds == 1 else f"{seconds} secondi")
    return " e ".join(parts)


class SetTimerSkill:
    metadata = {
        "intent": "SET_TIMER",
        "description": "Avvia un timer / conto alla rovescia di una certa durata (minuti e/o secondi), "
        "con un'etichetta opzionale ('timer per la pasta'). Usalo per 'metti un timer di 5 minuti', "
        "'timer di 30 secondi'. Diverso da SET_REMINDER, che ha un testo da ricordare.",
        "parameters": {
            "minutes": {"type": "integer", "required": False, "description": "Durata in minuti."},
            "seconds": {"type": "integer", "required": False, "description": "Durata in secondi (in aggiunta ai minuti)."},
            "label": {"type": "string", "required": False, "description": "A cosa serve il timer (es. 'pasta', 'uova'), se detto."},
        },
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        minutes = parameters.get("minutes") or 0
        seconds = parameters.get("seconds") or 0
        try:
            total = int(minutes) * 60 + int(seconds)
        except (TypeError, ValueError):
            return SkillResult(success=False, data={}, error="INVALID_VALUE")
        if total <= 0:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        label = (parameters.get("label") or "").strip()
        due = datetime.now(timezone.utc) + timedelta(seconds=total)
        text = label or "timer"
        self.reminder_manager.add(text, due, kind="timer")
        return SkillResult(success=True, data={
            "seconds_total": total, "duration": human_duration(total), "label": label,
            "due_at_local": due.astimezone().strftime("%H:%M:%S"),
        })


class CancelTimerSkill:
    metadata = {
        "intent": "CANCEL_TIMER",
        "description": "Annulla/ferma un timer attivo (l'ultimo impostato, o quello con l'etichetta indicata). "
        "Usalo per 'annulla il timer', 'togli il timer', 'ferma il conto alla rovescia'.",
        "parameters": {
            "label": {"type": "string", "required": False, "description": "Etichetta del timer da annullare, se detta."},
        },
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        label = (parameters.get("label") or "").strip()
        deleted = self.reminder_manager.delete_matching(label, kind="timer")
        if deleted is None:
            return SkillResult(success=False, data={"label": label}, error="NOT_FOUND")
        return SkillResult(success=True, data={"label": deleted["text"]})


class ListTimersSkill:
    metadata = {
        "intent": "LIST_TIMERS",
        "description": "Dice quali timer sono attivi e quanto manca a ciascuno. Usalo per 'quanto manca al timer', 'che timer ho'.",
        "parameters": {},
    }

    def __init__(self, reminder_manager):
        self.reminder_manager = reminder_manager

    def execute(self, parameters: dict = None):
        timers = self.reminder_manager.list_upcoming(kind="timer")
        if not timers:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        now = datetime.now(timezone.utc)
        entries = []
        for timer in timers:
            remaining = max(0, int((datetime.fromisoformat(timer["due_at"]) - now).total_seconds()))
            entries.append({"label": timer["text"], "remaining": human_duration(remaining), "remaining_seconds": remaining})
        return SkillResult(success=True, data={"timers": entries})
