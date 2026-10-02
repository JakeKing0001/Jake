"""Ciclo di vita: sospensione della proattivita', kill switch, drain delle richieste in corso e arresto ordinato.

Estratto da JakeCore (3.2 Reliability & Architecture): metodi spostati alla lettera, comportamento
invariato. Lavorano sullo stato di JakeCore tramite self, come prima."""
from __future__ import annotations

import contextlib
import time


class LifecycleMixin:
    @contextlib.contextmanager
    def proactivity_suspended(self, reason: str):
        """Per la durata del blocco Jake non prende iniziative: promemoria, automazioni e avvisi di
        sistema/housekeeping si fermano, e qualunque notifica arrivi comunque va in coda. Solo a
        runtime (nessuna configurazione persistente toccata); all'uscita ripartono SOLO i componenti
        che erano attivi (un kill switch resta rispettato) e si restituiscono i messaggi in coda ora
        ammessi. Gate hardware F2 (26/09/2026): notifiche partite durante la misura della voce."""
        components = [("scheduler", self.scheduler), ("trigger_scheduler", self.trigger_scheduler),
                      ("system_advisor", self.system_advisor)]
        paused = []
        self.notification_center.suspend()
        self.logger.info("Proattivita' sospesa: %s", reason)
        try:
            for name, component in components:
                thread = getattr(component, "_thread", None)
                if thread is not None and thread.is_alive():
                    try:
                        component.stop()
                        paused.append((name, component))
                    except Exception:
                        self.logger.exception("Errore sospendendo %s", name)
            released: list[str] = []
            yield released
        finally:
            for name, component in paused:
                if name != "system_advisor" and self.kill_switch.is_active():
                    continue  # il kill switch li ha voluti fermi: non si riaccendono da soli
                try:
                    component.start()
                except Exception:
                    self.logger.exception("Errore riprendendo %s", name)
            released.extend(self.notification_center.resume())
            self.logger.info("Proattivita' ripresa: %s", reason)

    def activate_kill_switch(self) -> None:
        """Ferma subito agenti e automazioni (F1, vedi core/kill_switch.py): il flag condiviso
        interrompe qualunque agente/piano PRIMA del passo successivo (mai a meta' di uno gia' in
        corso, vedi il modulo), e ReminderScheduler/TriggerScheduler vengono fermati per davvero
        (i loro thread terminano, non solo "smettono di fare qualcosa") - non ripartono da soli:
        serve reset_kill_switch() per farli ripartire."""
        self.kill_switch.activate()
        for scheduler in (self.scheduler, self.trigger_scheduler):
            try:
                scheduler.stop()
            except Exception:
                self.logger.exception("Errore fermando uno scheduler durante il kill switch")

    def reset_kill_switch(self) -> None:
        """Disattiva il kill switch e fa ripartire gli scheduler fermati da activate_kill_
        switch() - non riparte da sola: e' una scelta esplicita, cosi' come lo e' stata fermarli.

        F6: azzera anche il budget di autonomia (core/autonomy_budget.py) - se un'automazione
        impazzita aveva esaurito il budget prima o durante lo stop di emergenza, un "riprendi"
        esplicito dell'utente deve dare un budget pieno, non farla ripartire gia' bloccata senza
        che l'utente lo sappia."""
        self.kill_switch.reset()
        self.autonomy_budget.reset()
        for scheduler in (self.scheduler, self.trigger_scheduler):
            try:
                scheduler.start()
            except Exception:
                self.logger.exception("Errore riavviando uno scheduler dopo il kill switch")

    _DRAIN_TIMEOUT_SECONDS = 5.0

    _DRAIN_POLL_SECONDS = 0.05

    def _drain_in_flight_answers(self) -> None:
        """F1.8.4 ("gestire shutdown con drain limitato"): aspetta, entro un tetto, che ogni
        answer() gia' in corso su un ALTRO thread (tipicamente una richiesta companion - core/
        companion_server.py e' un ThreadingHTTPServer con un thread per richiesta; il loop voce
        non si sovrappone mai con la propria chiamata a shutdown(), che arriva sempre DOPO che il
        proprio answer() e' gia' tornato) finisca, PRIMA di fermare gli altri componenti/salvare
        le cache che quella chiamata potrebbe ancora star usando - altrimenti una richiesta a
        meta' potrebbe scrivere una cache DOPO il salvataggio "finale" qui sotto, o usare un
        componente gia' fermato. `companion_server.stop()` e' gia' stato chiamato PRIMA di questo
        (smette di accettare richieste NUOVE): qui si aspetta solo quelle GIA' in corso. Non
        blocca per sempre: un tetto di 5s (poi procede comunque, con un avviso nel log) - lo
        stesso principio "chiedi gentilmente, poi procedi" gia' usato per gli scheduler in
        background (F1.8.5) e per il worker sandboxato (F1.6)."""
        deadline = time.monotonic() + self._DRAIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            with self._in_flight_lock:
                if self._in_flight_answers == 0:
                    return
            time.sleep(self._DRAIN_POLL_SECONDS)
        with self._in_flight_lock:
            remaining = self._in_flight_answers
        if remaining > 0:
            self.logger.warning(
                "Shutdown: %d chiamata/e ad answer() ancora in corso dopo %.1fs, procedo comunque",
                remaining, self._DRAIN_TIMEOUT_SECONDS,
            )

    def shutdown(self) -> None:
        # F1.8.4 ("gestire shutdown con drain limitato, checkpoint e release dei device"): buco
        # reale - un `except Exception: pass` silenzioso per ognuno di questi passi significava
        # che un component.stop() fallito (una connessione companion non chiusa, un hook
        # desktop_context non rimosso...) o un salvataggio di cache fallito (disco pieno,
        # permessi) sparivano senza lasciare TRACCIA in nessun log: un utente che si accorge che
        # NEST "dimentica" la cache dopo un riavvio, o che un socket resta occupato, non avrebbe
        # avuto modo di scoprire perche'. Ogni passo di chiusura logga ora l'eccezione con il
        # nome del componente prima di continuare con gli altri (non ferma lo shutdown: un
        # componente che non si chiude bene non deve impedire agli altri di provarci).
        #
        # F4.8.2: l'HUD nativo si chiude prima del server a cui e' collegato (niente riconnessioni a vuoto).
        fallback = getattr(self, "_ready_fallback", None)
        if fallback is not None:
            fallback.cancel()
        native_hud = getattr(self, "native_hud", None)
        if native_hud is not None:
            try:
                native_hud.stop()
            except Exception:
                self.logger.exception("Errore chiudendo l'HUD nativo durante lo shutdown")
            self._revoke_native_hud_credential()
        # companion_server e' fermato PER PRIMO E DA SOLO (non nel loop sotto): smette di
        # accettare richieste NUOVE prima che _drain_in_flight_answers() aspetti quelle GIA' in
        # corso - l'ordine conta, altrimenti una richiesta potrebbe iniziare proprio mentre si
        # aspetta che le altre finiscano, vanificando il senso del drain.
        try:
            self.companion_server.stop()
        except Exception:
            self.logger.exception("Errore chiudendo companion_server durante lo shutdown")
        self._drain_in_flight_answers()
        for name, component in (
            ("scheduler", self.scheduler), ("trigger_scheduler", self.trigger_scheduler),
            ("system_advisor", self.system_advisor), ("desktop_context", self.desktop_context),
        ):
            try:
                component.stop()
            except Exception:
                self.logger.exception("Errore chiudendo %s durante lo shutdown", name)
        try:
            self.retriever.example_index.save_cache()
            self.retriever.capability_index.save_cache()
        except Exception:
            self.logger.exception("Errore salvando la cache degli indici semantici durante lo shutdown")
        # F1.6: nessun worker da fermare se nessuna skill forgiata e' mai stata invocata in
        # questa sessione (stop_sandbox_worker() e' un no-op in quel caso) - se invece il worker
        # sandboxato e' vivo, deve essere chiuso esplicitamente qui: non e' un processo figlio
        # che Windows terminerebbe da solo alla chiusura di Jake.
        try:
            self.skill_registry.stop_sandbox_worker()
        except Exception:
            self.logger.exception("Errore fermando il worker sandboxato per le skill forgiate durante lo shutdown")
        # F1.4.6 (fase 6): chiude la connessione SQLite del registro credenziali - stesso
        # principio degli altri passi sopra, un fallimento qui non deve impedire al resto dello
        # shutdown di proseguire.
        try:
            self.device_credential_store.close()
        except Exception:
            self.logger.exception("Errore chiudendo device_credential_store durante lo shutdown")
