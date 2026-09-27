import threading
from datetime import datetime, timedelta

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
            "minutes": {
                "type": "integer", "required": False,
                "description": "Solo se l'utente dice per quanto ('per 30 minuti', 'per un'ora'): poi si torna alla "
                               "modalita' di prima da soli.",
            },
        },
    }

    def __init__(self, notification_center, on_restored=None, timer_factory=threading.Timer, clock=datetime.now):
        self.notification_center = notification_center
        # F6.5.6: callable(modalita' di prima, messaggi trattenuti) quando la modalita' a tempo finisce da sola
        self.on_restored = on_restored
        self._timer_factory = timer_factory
        self._clock = clock
        self._timer = None
        self._lock = threading.Lock()

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw = (parameters.get("mode") or "").strip().lower()
        try:
            mode = NotificationMode(raw)
        except ValueError:
            return SkillResult(success=False, data={"mode": raw}, error="INVALID_VALUE")
        minutes = parameters.get("minutes")
        timed = isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and 0 < minutes <= 24 * 60

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()  # una nuova scelta sostituisce il ritorno automatico precedente
                self._timer = None
            previous = self.notification_center.mode
            released = self.notification_center.set_mode(mode)
            data = {"mode": mode.value, "released": released}
            if timed and mode != previous:
                timer = self._timer_factory(minutes * 60, self._restore, args=(mode, previous))
                timer.daemon = True
                self._timer = timer
                timer.start()
                data["until"] = (self._clock() + timedelta(minutes=minutes)).strftime("%H:%M")
                data["previous"] = previous.value
        return SkillResult(success=True, data=data)

    def _restore(self, timed_mode: NotificationMode, previous: NotificationMode) -> None:
        """F6.5.6: alla fine torna la modalita' di prima - solo se nel frattempo l'utente non ne ha scelta un'altra."""
        with self._lock:
            self._timer = None
            if self.notification_center.mode != timed_mode:
                return
            released = self.notification_center.set_mode(previous)
        if self.on_restored is not None:
            self.on_restored(previous, released)


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
