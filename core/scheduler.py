import threading
import time


class ReminderScheduler:
    """Controlla periodicamente i promemoria scaduti (v1.2: Jake proattivo) su un thread separato,
    e invoca un callback per ciascuno. Il chiamante decide come notificare (stampa, voce, toast)."""

    def __init__(self, reminder_manager, on_due=None, interval_seconds: float = 20):
        self.reminder_manager = reminder_manager
        self.on_due = on_due
        self.interval_seconds = interval_seconds
        self._thread = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            for reminder in self.reminder_manager.due_reminders():
                if self.on_due is not None:
                    try:
                        self.on_due(reminder)
                    except Exception:
                        pass
            self._stop_event.wait(self.interval_seconds)
