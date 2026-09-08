import threading

from core.logger import get_logger


class ReminderScheduler:
    """Controlla periodicamente i promemoria scaduti (v1.2: Jake proattivo) su un thread separato,
    e invoca un callback per ciascuno. Il chiamante decide come notificare (stampa, voce, toast)."""

    def __init__(self, reminder_manager, on_due=None, interval_seconds: float = 20):
        self.reminder_manager = reminder_manager
        self.on_due = on_due
        self.interval_seconds = interval_seconds
        self._thread = None
        self._stop_event = threading.Event()
        self._logger = get_logger()

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
            # Un errore qui (es. sqlite occupato per un istante, o il callback che fallisce)
            # non deve mai terminare il thread per sempre: verrebbe notificato in silenzio e
            # nessun promemoria futuro scatterebbe piu' finche' Jake non viene riavviato.
            try:
                for reminder in self.reminder_manager.due_reminders():
                    if self.on_due is not None:
                        try:
                            self.on_due(reminder)
                        except Exception:
                            self._logger.exception(
                                "Errore nel callback di notifica del promemoria %s", reminder.get("id")
                            )
            except Exception:
                self._logger.exception("Errore controllando i promemoria scaduti")
            self._stop_event.wait(self.interval_seconds)
