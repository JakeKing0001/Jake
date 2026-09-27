import threading
from datetime import datetime, timedelta, timezone

from core.logger import get_logger


class ReminderScheduler:
    """Controlla periodicamente i promemoria scaduti (v1.2: Jake proattivo) su un thread separato,
    e invoca un callback per ciascuno. Il chiamante decide come notificare (stampa, voce, toast)."""

    def __init__(self, reminder_manager, on_due=None, interval_seconds: float = 20, stop_timeout_seconds: float = 2.0):
        self.reminder_manager = reminder_manager
        self.on_due = on_due
        # F6.1.5: callable(lista di promemoria) per quelli scaduti da tempo (Jake spento, PC in sospensione): UN
        # riepilogo invece di una raffica. Senza, ognuno passa da on_due come prima.
        self.on_missed = None
        # F6.3: chiamato a ogni giro dopo i promemoria (JakeCore lo usa per il riepilogo delle
        # notifiche rimandate). Un errore qui non ferma mai il ciclo.
        self.on_tick = None
        self.interval_seconds = interval_seconds
        # F1.8.5: configurabile solo per i test (verificare un thread che non si ferma in tempo
        # senza dover davvero aspettare i 2s reali di default) - il comportamento di produzione
        # resta invariato.
        self._stop_timeout_seconds = stop_timeout_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._logger = get_logger()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # F1.8.5 ("aggiungere deadlock timeout e diagnosi"): buco reale, riprodotto per davvero -
        # se il ciclo era bloccato dentro due_reminders()/un callback lento oltre i 2s di
        # timeout, join() tornava comunque, silenziosamente, senza dire che il thread era ANCORA
        # vivo. Chi chiama stop() (JakeCore.shutdown(), gia' reso rumoroso sui propri fallimenti
        # in questa sessione) non aveva modo di scoprire che lo scheduler non si era davvero
        # fermato, ne' che start() lo avrebbe poi lasciato in esecuzione insieme al nuovo thread
        # se richiamato (start() controlla is_alive(), quindi non ne crea uno doppio - ma quello
        # vecchio, bloccato, resta comunque attivo e invisibile).
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._stop_timeout_seconds)
            if self._thread.is_alive():
                self._logger.warning(
                    "ReminderScheduler non si e' fermato entro il timeout: il thread precedente "
                    "e' ancora in esecuzione (probabilmente bloccato in due_reminders() o in un "
                    "callback lento)."
                )

    @property
    def missed_after(self) -> timedelta:
        """Oltre questo ritardo un promemoria non e' "appena scaduto" ma perso (Jake non girava): 3 giri, almeno 2 min."""
        return timedelta(seconds=max(120.0, 3 * self.interval_seconds))

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        missed: list[dict] = []
        timely: list[dict] = []
        for reminder in self.reminder_manager.due_reminders():
            try:
                due_at = datetime.fromisoformat(reminder["due_at"])
                if due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=timezone.utc)
                late = now - due_at > self.missed_after
            except (KeyError, TypeError, ValueError):
                late = False
            (missed if late else timely).append(reminder)
        if missed and self.on_missed is not None:
            try:
                self.on_missed(missed)
            except Exception:
                self._logger.exception("Errore nel riepilogo dei promemoria persi")
        else:
            timely = missed + timely
        for reminder in timely:
            if self.on_due is not None:
                try:
                    self.on_due(reminder)
                except Exception:
                    self._logger.exception("Errore nel callback di notifica del promemoria %s", reminder.get("id"))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            # Un errore qui (es. sqlite occupato per un istante, o il callback che fallisce)
            # non deve mai terminare il thread per sempre: verrebbe notificato in silenzio e
            # nessun promemoria futuro scatterebbe piu' finche' Jake non viene riavviato.
            try:
                self.tick()
            except Exception:
                self._logger.exception("Errore controllando i promemoria scaduti")
            if self.on_tick is not None:
                try:
                    self.on_tick()
                except Exception:
                    self._logger.exception("Errore nel giro periodico dello scheduler")
            self._stop_event.wait(self.interval_seconds)
