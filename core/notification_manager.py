"""Notifiche proattive di Jake: gate, coda, riepilogo, promemoria scaduti e prontezza all'avvio (F6.1/F6.3).

Estratto da JakeCore (3.2 Reliability & Architecture): metodi spostati alla lettera, comportamento
invariato. Lavorano sullo stato di JakeCore tramite self, come prima."""
from __future__ import annotations

import threading
import time
from core.hud_protocol import EventType, HudEvent
from core.proactive_gate import DELIVER, DUPLICATE, MUTED, notification_key


class NotificationMixin:
    last_notification: dict | None

    # F6.2.7: perche' esiste una notifica, quando chi la produce non lo dice (vedi `source` di notify)
    NOTIFICATION_SOURCES = {
        "reminder": "e' un promemoria che hai chiesto tu",
        "advisory": "e' un controllo automatico dello stato del PC (batteria, disco, attivita' dimenticate)",
        "trigger": "e' il risultato di un'automazione che hai programmato",
        "pairing": "un dispositivo nuovo ha chiesto di collegarsi a Jake",
    }

    def notify(self, kind: str, message: str, *, trace_id: str | None = None, critical: bool = False,
               source: str | None = None) -> str | None:
        """Punto unico da cui passa ogni notifica proattiva (promemoria/avviso/automazione)
        prima di essere presentata, sia in CLI (qui sotto) sia in voce (vedi WakeWordSession,
        core/voice/wake_word_session.py, che chiama questo stesso metodo): applica la modalita'
        di notifica corrente (v4.3). Restituisce il messaggio da presentare subito, o None se
        e' stato solo messo in coda per quando la modalita' tornera' a permetterlo.

        F1.7.2: `trace_id` (opzionale - solo un'automazione ne ha gia' uno reale, vedi
        `_default_on_trigger_fired`) collega l'evento HUD alle ricevute nel ledger che la stessa
        esecuzione ha gia' prodotto - senza, una notifica "Ho eseguito X" non aveva NESSUN modo
        di essere ricollegata a cosa e' successo davvero. Non propagato al percorso in coda
        (`NotificationCenter._queued`/`set_mode()`): una notifica rimandata riemerge oggi solo
        dentro il risultato testuale di `SET_NOTIFICATION_MODE`, mai come un secondo `HudEvent` -
        non c'e' un evento successivo a cui riattaccare il trace_id, dichiarato apertamente."""
        gated = self.notification_center.gate(kind, message)
        if gated is not None and kind == "advisory" and not critical and not getattr(self, "ready_for_notifications", True):
            self.notification_center.defer(kind, gated)
            self.logger.info("Notifica %s rimandata: Jake non ha ancora finito di avviarsi", kind)
            return None
        gate = getattr(self, "proactive_gate", None)
        if gated is not None and gate is not None:
            # F6.1/F6.3: un duplicato si scarta; budget, quiet hours o conversazione in corso -> in coda
            outcome, reason = gate.check(kind, gated, critical=critical)
            if outcome != DELIVER:
                if outcome not in (DUPLICATE, MUTED):
                    self.notification_center.defer(kind, gated)
                self.logger.info("Notifica %s %s: %s", kind, "rimandata" if outcome not in (DUPLICATE, MUTED) else "scartata", reason)
                return None
        if gated is not None:
            # l'ultima notifica mostrata: a lei si riferiscono "meno notifiche cosi'", "non mostrarmelo piu'", "rimandala"
            self.last_notification = {"kind": kind, "message": gated, "key": notification_key(kind, gated),
                                      "source": source or self.NOTIFICATION_SOURCES.get(kind, ""), "at": time.time()}
            responder = self._remote_responder()
            payload = {"kind": kind, "text": gated}
            if responder:
                payload["responder"] = responder
            self.event_bus.publish(HudEvent(EventType.NOTIFICATION, payload, trace_id=trace_id))
            if responder:
                # F7.4 (un solo active responder): con un telefono attivo la notifica la riceve lui dal bus; il PC non la
                # dice ne' la stampa - niente doppia risposta, niente voce in una stanza magari vuota
                self.logger.info("Notifica %s consegnata al dispositivo attivo %s, non al PC", kind, responder)
                return None
        return gated

    def _remote_responder(self) -> str | None:
        """Il dispositivo companion che risponde adesso al posto del PC, se c'e' (F7.4: DeviceRegistry col lease)."""
        server = getattr(self, "companion_server", None)
        if server is None or getattr(server, "running", False) is not True:
            return None
        try:
            active = server.devices.active_device_id
        except Exception:
            self.logger.exception("Errore leggendo il dispositivo attivo")
            return None
        return active if isinstance(active, str) and active else None

    # Dopo una risposta si aspetta questo tempo prima di un riepilogo: la voce potrebbe ancora parlare.
    DIGEST_QUIET_AFTER_ANSWER_S = 60.0

    DIGEST_MAX_ITEMS = 3

    def _scheduler_tick(self) -> None:
        """Un giro periodico dello scheduler: notifiche rimandate (F6.3) e scelta del modello (F8.4.4)."""
        self.release_deferred_notifications()
        try:
            self._route_models_tick()
        except Exception:
            self.logger.exception("Errore ricontrollando la scelta del modello")

    def release_deferred_notifications(self, prefix: str = "Mentre eri impegnato: ") -> str | None:
        """F6.3: le notifiche rimandate dal gate (budget, quiet hours, conversazione) non restano in coda
        per sempre. Appena le condizioni lo permettono escono come UN riepilogo attraverso il canale degli
        avvisi (stampa in CLI, voce nella sessione vocale). Ritorna il riepilogo consegnato, o None."""
        gate = getattr(self, "proactive_gate", None)
        center = getattr(self, "notification_center", None)
        if gate is None or center is None or center.suspended:
            return None
        if gate.conversation_active() or gate.in_quiet_hours() or not gate.budget_available():
            return None  # il riepilogo stesso deve poter passare, o verrebbe rimandato di nuovo
        if time.time() - getattr(self, "_last_answer_finished_at", 0.0) < self.DIGEST_QUIET_AFTER_ANSWER_S:
            return None
        items = center.take_deferred()
        if not items:
            return None
        shown = [item["message"].rstrip(".") for item in items[: self.DIGEST_MAX_ITEMS]]
        digest = prefix + "; ".join(shown) + "."
        if len(items) > self.DIGEST_MAX_ITEMS:
            digest += f" E altre {len(items) - self.DIGEST_MAX_ITEMS} notifiche."
        callback = getattr(getattr(self, "system_advisor", None), "on_advisory", None) or self._default_on_advisory
        callback(digest)
        return digest

    def present_notification(self, kind: str, message: str, source: str | None = None) -> str | None:
        """F6.1: UNA strada per mostrare una notifica - gate (modalita', duplicati, budget, quiet hours,
        preferenze) e poi un unico presentatore: stampa in CLI, voce nella sessione vocale (che la rimanda se
        Jake sta parlando). Usata dalle fonti nuove (es. WATCH_PROCESS) invece di un callback per ciascuna."""
        gated = self.notify(kind, message, source=source)
        if gated is None:
            return None
        presenter = getattr(self, "notification_presenter", None)
        if presenter is not None:
            try:
                presenter(kind, gated)
            except Exception:
                self.logger.exception("Errore presentando una notifica")
        else:
            print(f"\nJake > {gated}\nTu > ", end="", flush=True)
        return gated

    def _on_missed_reminders(self, reminders: list[dict]) -> None:
        """F6.1.5: promemoria scaduti mentre Jake era spento (o il PC in sospensione): un solo riepilogo, con l'ora
        a cui erano previsti, invece di una raffica di notifiche al riavvio."""
        from datetime import datetime as _datetime

        parts = []
        for reminder in reminders:
            try:
                at = _datetime.fromisoformat(reminder["due_at"]).astimezone().strftime("%H:%M")
            except (KeyError, ValueError):
                at = ""
            parts.append(f"{reminder.get('text') or 'timer'}" + (f" (alle {at})" if at else ""))
        count = len(reminders)
        head = "è scaduto un promemoria" if count == 1 else f"sono scaduti {count} promemoria"
        self.present_notification("reminder", f"Mentre non ero attivo {head}: " + "; ".join(parts) + ".",
                                  source="sono promemoria scaduti mentre Jake non era attivo")

    @staticmethod
    def reminder_source(reminder: dict) -> str:
        what = "un timer" if reminder.get("kind") == "timer" else "un promemoria"
        return f"e' {what} che hai impostato tu"

    def _default_on_reminder_due(self, reminder: dict) -> None:
        message = self.notify("reminder", self.format_due_reminder(reminder), source=self.reminder_source(reminder))
        if message is None:
            return
        print(f"\nJake > {message}\nTu > ", end="", flush=True)

    @staticmethod
    def format_due_reminder(reminder: dict) -> str:
        if reminder.get("kind") == "timer":
            label = reminder.get("text") or "timer"
            return "Il timer è scaduto!" if label == "timer" else f"Il timer per {label} è scaduto!"
        return f"Promemoria: {reminder['text']}"

    READY_FALLBACK_S = 120.0

    READY_SETTLE_S = 4.0  # dopo "sono pronto": il tempo di un saluto prima degli avvisi rimasti in coda

    def mark_ready(self) -> None:
        """La sessione ha finito di avviarsi (microfono aperto, modello vocale caricato, prompt pronto)."""
        if getattr(self, "ready_for_notifications", True):
            return
        self.ready_for_notifications = True
        fallback = getattr(self, "_ready_fallback", None)
        if fallback is not None:
            fallback.cancel()
        timer = threading.Timer(self.READY_SETTLE_S, self.release_deferred_notifications,
                                kwargs={"prefix": "All'avvio ho notato: "})
        timer.daemon = True
        timer.start()

    def _default_on_advisory(self, message: str) -> None:
        gated = self.notify("advisory", message)
        if gated is None:
            return
        print(f"\nJake > {gated}\nTu > ", end="", flush=True)

    def _publish_notification_state(self, mode, pending: int) -> None:
        from core.notification_center import MODE_LABELS_IT

        self.event_bus.publish(HudEvent(EventType.NOTIFICATION_STATE, {
            "mode": mode.value, "mode_label": MODE_LABELS_IT.get(mode, mode.value), "pending": pending}))

    def _timed_notification_mode_ended(self, previous, released: list[str]) -> None:
        """F6.5.6: "non disturbare per 30 minuti" e' finito da solo. Lo si dice, con cio' che e' stato trattenuto,
        dalla stessa strada delle altre notifiche (e' una richiesta esplicita dell'utente: puntuale come un promemoria)."""
        from core.notification_center import MODE_LABELS_IT

        message = f"Tempo scaduto: torno alla modalità {MODE_LABELS_IT.get(previous, previous.value)}."
        if released:
            message += " Nel frattempo: " + " ".join(released)
        self.present_notification("reminder", message, source="avevi scelto una modalita' di notifica a tempo")
