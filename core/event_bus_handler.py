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
