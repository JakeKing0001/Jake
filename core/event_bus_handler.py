"""Pubblicazione sull'event bus verso HUD e companion: passi, prove d'effetto, ricevute, conferme in sospeso, undo.

Estratto da JakeCore (3.2 Reliability & Architecture): metodi spostati alla lettera, comportamento
invariato. Lavorano sullo stato di JakeCore tramite self, come prima."""
from __future__ import annotations

from core.hud_protocol import EventType, HudEvent
from core.risk import risk_of


class EventPublishingMixin:
    def _publish_plan_outcome_effect_proof_events(self, outcome) -> None:
        """Estrae da un `PlanOutcome` (core/plan_executor.py) le stesse due liste che
        `_publish_effect_proof_events` sotto si aspetta, condivisa dai due chiamanti che
        ricevono un PlanOutcome (`_try_plan`, `_default_on_trigger_fired`) invece di ripetere la
        stessa estrazione due volte. `outcome.trace_id` (F1.7.2) e' gia' lo stesso che correla
        ogni passo del piano nel ledger - propagato qui (F4.1.1) cosi' l'evento sul bus porta la
        stessa correlazione, non solo la ricevuta scritta su disco."""
        verified_steps = [
            (step_outcome.step.intent, step_outcome.verified)
            for step_outcome in (*outcome.completed, *([outcome.stopped_step] if outcome.stopped_step else []))
            if step_outcome.verified is not None
        ]
        self._publish_effect_proof_events(
            verified_steps, [step_outcome.step.intent for step_outcome in outcome.rolled_back],
            trace_id=outcome.trace_id,
        )

    def _publish_effect_proof_events(
        self, verified_steps: list[tuple[str, str]], rolled_back_intents: list[str], *, trace_id: str | None = None,
    ) -> None:
        """F1.3.8 ("esporre undo e prove a HUD/companion tramite eventi versionati"): prima di
        questo, un rollback (core/execution_safety.py::rollback_effect) o una verifica
        indipendente dell'effetto (F1.3.3, verify_effect) erano visibili SOLO nel ledger
        (data/jake_ledger.jsonl) - un HUD o un'app companion non aveva modo di saperlo in tempo
        reale, solo rileggendo il ledger dopo. Chiamato sia dal percorso agente
        (core/agent.py::AgentOutcome) sia dal percorso piano (core/plan_executor.py::
        PlanOutcome), che espongono la stessa informazione con forme leggermente diverse -
        l'estrazione resta al chiamante, qui solo la pubblicazione condivisa. Nessun evento
        quando non c'e' nulla da riportare (nessun intent verificabile in questo turno, nessun
        rollback) - non aggiunge rumore al caso comune."""
        for intent in rolled_back_intents:
            self.event_bus.publish(HudEvent(EventType.UNDO, {"intent": intent}, trace_id=trace_id))
        for intent, verified in verified_steps:
            self.event_bus.publish(HudEvent(EventType.VERIFICATION, {"intent": intent, "verified": verified}, trace_id=trace_id))

    def _publish_task_event(self, action) -> None:
        """F6.3/F6.7: avvolge OGNI chiamata al ponte task monitor/notifiche usata da _run_agent (decisione
        richiesta, fine del compito) - un errore qui (bridge, notification_policy, disco) non deve MAI
        impedire a Jake di rispondere, stesso principio gia' applicato a _on_agent_step_completed sopra."""
        try:
            action()
            self.task_monitor_store.save(self.task_monitor)
        except Exception:
            self.logger.exception("Errore nel ponte task monitor / notifiche")

    def _publish_hud_event(self, event: HudEvent) -> None:
        """Punto unico per lo stato del turno verso HUD/companion. Lo stato e' un effetto collaterale del runtime, non
        parte della logica: senza bus (core parziali, avvio, fallback) si salta, e un iscritto che fallisce non rompe
        il turno. La privacy resta del bus (EventBus.redactor, F4.5.7) e di answer(), che decide QUANDO il contenuto
        esce. Contratto del turno:
        - THINKING {} all'inizio, EXECUTING {} quando parte una skill autorizzata, THINKING {"status"} se il modello
          tarda, ERROR {"detail"} generico: solo stato, mai il testo della richiesta o della risposta;
        - USER_MESSAGE poi JAKE_MESSAGE solo a turno concluso, non annullato e non privato (sono il contenuto: l'HUD
          conosce gia' la richiesta, l'ha scritta lui o l'ha vista nel TRANSCRIPT della voce);
        - senza JAKE_MESSAGE (privato, risposta vuota, uscita) un IDLE {} chiude lo stato;
        - turno annullato: dopo l'annullamento il core non pubblica nulla; lo stato finale e' di chi l'ha annullato
          (la sessione vocale, l'unica che puo' farlo: _finish_turn torna a IDLE solo se nessuno stato piu' nuovo
          ha preso il posto del turno)."""
        publish = getattr(getattr(self, "event_bus", None), "publish", None)
        if publish is None:
            return
        try:
            publish(event)
        except Exception:
            logger = getattr(self, "logger", None)
            if logger is not None:
                logger.exception("Errore pubblicando lo stato %s verso l'HUD", event.type.value)

    def _publish_pending_confirmation(self, action: dict | None) -> None:
        """F4.4.1/F4.5.3: stato "waiting" e permission card dell'HUD. Solo metadati dell'azione in
        sospeso (intent, motivo, rischio, fonte esterna), mai i suoi parametri."""
        if action is None:
            payload: dict = {"pending": False}
        else:
            intent = str(action.get("intent") or "")
            try:
                risk = risk_of(intent).value if intent else ""
            except Exception:
                risk = ""
            payload = {
                "pending": True, "intent": intent, "reason": str(action.get("reason") or "confirmation_required"),
                "risk": risk, "external_source": bool(action.get("suggested_by_external_content")),
            }
        trace_id = action.get("trace_id") if isinstance(action, dict) else None
        self.event_bus.publish(HudEvent(EventType.CONFIRMATION, payload,
                                        trace_id=trace_id if isinstance(trace_id, str) else None))

    def _publish_action_receipt(self, receipt) -> None:
        """F4.5/F4.6.1: ogni ricevuta del ledger ha una rappresentazione nell'HUD (action center)."""
        self._observe_forge_trial(receipt)
        result = str(getattr(receipt, "result", "") or "")
        requested_by = str(getattr(receipt, "requested_by", "") or "")
        self.event_bus.publish(HudEvent(EventType.ACTION_RECEIPT, {
            "action_id": receipt.action_id, "intent": receipt.intent,
            "requested_by": requested_by.split(":", 1)[0],
            "outcome": "success" if result == "success" else "failed",
            "error_category": str(getattr(receipt, "error_category", "") or ""),
            "verified": str(getattr(receipt, "verified", "") or ""),
        }, trace_id=getattr(receipt, "trace_id", None)))

    def _observe_forge_trial(self, receipt) -> None:
        """F8.3.7: le skill forgiate in prova vedono ogni loro esecuzione reale; a prova finita (superata o skill
        disattivata) l'utente lo sa dal canale degli avvisi (voce nella sessione vocale, CLI altrimenti)."""
        forge = getattr(self, "skill_forge", None)
        if forge is None or getattr(forge, "skill_store", None) is None:
            return
        try:
            message = forge.observe_execution(str(receipt.intent or ""), str(getattr(receipt, "result", "") or ""),
                                              str(getattr(receipt, "error_category", "") or ""))
        except Exception:
            self.logger.exception("Errore nel periodo di prova di una skill forgiata")
            return
        if message:
            callback = getattr(getattr(self, "system_advisor", None), "on_advisory", None) or self._default_on_advisory
            try:
                callback(message)
            except Exception:
                self.logger.exception("Errore annunciando la fine della prova di una skill forgiata")

    def _publish_undo_available(self, descriptor) -> None:
        """F4.6.3: scadenza dell'undo visibile nell'HUD (mai i parametri compensatori)."""
        self.event_bus.publish(HudEvent(EventType.UNDO_AVAILABLE, {
            "action_id": descriptor.action_id, "compensating_intent": descriptor.compensating_intent,
            "expires_at": float(descriptor.expires_at),
        }))

    # Baseline pre-sperimentazione: stati registrati nella timeline (SPEAKING = JAKE_MESSAGE senza testo)
    _TRACED_STATES = frozenset({"IDLE", "LISTENING", "TRANSCRIBING", "THINKING", "EXECUTING", "ERROR", "PAUSED",
                                "DICTATION"})

    def _record_state_event(self, event) -> None:
        """Osservatore dell'event bus (mai chiamato in modalita' privata): solo il NOME dello stato, nessun contenuto."""
        from core.logger import log_state
        from core.request_context import current_trace_id

        kind = getattr(getattr(event, "type", None), "value", None)
        payload = getattr(event, "payload", None) or {}
        if kind == "JAKE_MESSAGE":
            if payload.get("text"):
                return
            kind = "SPEAKING"
        elif kind not in self._TRACED_STATES:
            return
        log_state(kind, trace_id=current_trace_id())

    def _log_turn_summary(self, started: float, outcome: str) -> None:
        """Una riga per turno in jake_actions.jsonl: niente testo dell'utente, niente valori di ricordi o parametri."""
        import time

        from core.logger import log_turn
        from core.request_context import (
            current_conversation_channel,
            current_stt_confidence,
            current_stt_ms,
            current_turn_trace,
        )

        turn = dict(current_turn_trace() or {})
        self.last_trace_id = None if self.private_mode else turn.get("trace_id")
        if self.private_mode:
            log_turn({}, private=True)
            return
        timings = dict(turn.pop("timings_ms", {}))
        timings["total"] = round((time.monotonic() - started) * 1000, 1)
        stt_ms = current_stt_ms()
        if stt_ms is not None:
            timings["stt"] = round(stt_ms, 1)
        confidence = current_stt_confidence()
        record = {
            **turn,
            "outcome": outcome,
            "channel": current_conversation_channel() or "local",
            "stt_confidence": round(confidence, 3) if isinstance(confidence, float) else None,
            "timings_ms": timings,
            # retrieved = ricordi messi nel contesto; used = citati davvero dalla risposta quando la skill lo sa
            "memory_used": bool(turn["memory_cited"]) if "memory_cited" in turn else bool(turn.get("memory")),
            "rss_mb": _rss_mb(),
        }
        record.setdefault("route", "other")
        log_turn(record)


def _rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 1e6, 1)
    except Exception:
        return None
