"""L'agente a passi dentro JakeCore: contesto e ricordi per il prompt, avanzamento dei passi, esecuzione, ripresa dopo una domanda e dopo un'interruzione.

Estratto da JakeCore (3.2 Reliability & Architecture): metodi spostati alla lettera, comportamento
invariato. Lavorano sullo stato di JakeCore tramite self, come prima."""
from __future__ import annotations

import time
from core.agent_checkpoint import AgentCheckpoint
from core.command import Command
from core.hud_protocol import EventType, HudEvent
from core.logger import new_trace_id
from core.request_context import current_device_id, current_session_id


class AgentMixin:
    def _agent_memories(self, request: str) -> str:
        """F5.5 (agente): gli stessi ricordi pertinenti delle risposte libere (core/memory_manager.py::relevant_for,
        con budget di caratteri), con la loro fonte, per i compiti degli agenti."""
        from core.response_formatter import memory_provenance

        memory = getattr(self, "memory_manager", None)
        if memory is None or not hasattr(memory, "relevant_for"):
            return ""
        return "\n".join(f"- {m['key']}: {m['value']}{memory_provenance(m)}" for m in memory.relevant_for(request))

    def _agent_context(self) -> str:
        parts = [part for part in (self.desktop_context.context_summary(), self.conversation_state.entities_summary()) if part]
        return " | ".join(parts)

    def _on_agent_step(self, step_index: int, description: str) -> None:
        self.session_hooks.call("set_state", "working", description)
        # F4.5.2: il passo in corso; l'esito e la durata arrivano da _on_agent_step_completed
        self._running_step: tuple[int, str, float] | None = (step_index, description, time.monotonic())
        self.event_bus.publish(HudEvent(EventType.AGENT_STEP, {"step": step_index, "description": description,
                                                               "status": "running"}))

    def _on_agent_step_completed(self, outcome) -> None:
        """F1.8.4 ("checkpoint... da cui riprendere"): collegato a `on_step_completed` di
        ciascuno dei tre TaskAgent (general/coding/research, vedi __init__) - chiamato dopo OGNI
        passo che l'agente completa (riuscito o fallito), sovrascrive il checkpoint sul disco con
        il progresso aggiornato. `outcome.trace_id`/`.request`/`.agent_name` sono popolati da
        `TaskAgent.run()` stesso (F1.8.4) - `agent_name` conta DAVVERO qui (non solo "general"
        come nella prima fetta): un checkpoint salvato con l'agente sbagliato riprenderebbe il
        compito con la persona/gli strumenti fissi sbagliati (vedi core/orchestrator.py). Solo
        intent/parametri/esito di ogni passo vengono salvati (mai l'intero `SkillResult` - i dati
        grezzi di una skill potrebbero contenere contenuto esterno/sensibile che non ha senso
        duplicare su un secondo file, il ledger e' gia' la fonte di verita' per quello)."""
        running, self._running_step = getattr(self, "_running_step", None), None
        if running is not None and outcome.steps:
            # F4.5.2: piano e passi live - esito e durata del passo appena finito (un passo senza parametri non
            # e' mai partito: non ha un "running" da chiudere)
            step_index, description, started = running
            last = outcome.steps[-1]
            self.event_bus.publish(HudEvent(EventType.AGENT_STEP, {
                "step": step_index, "description": description,
                "status": "done" if last.result is not None and last.result.success else "failed",
                "duration_ms": round((time.monotonic() - started) * 1000),
            }))
        if outcome.trace_id is None or outcome.request is None or outcome.agent_name is None:
            return
        checkpoint = AgentCheckpoint(
            trace_id=outcome.trace_id, agent_name=outcome.agent_name, request=outcome.request,
            completed_steps=[
                {
                    "intent": step.intent, "parameters": step.parameters,
                    "success": bool(step.result and step.result.success),
                }
                for step in outcome.steps
            ],
        )
        try:
            self.agent_checkpoints.save(checkpoint)
        except OSError:
            self.logger.exception("Errore salvando il checkpoint del compito in corso")

        # F6.7 (adozione, stesso hook della riga sopra): segue il compito in silenzio (F6.7.1,
        # nessun evento a ogni passo) e salva lo snapshot dei compiti ancora in corso - un
        # monitor RUNNING sopravvive a un'interruzione anomala (F6.7.7), senza alcuna ripresa
        # automatica (vedi il docstring di core/task_notification_bridge.py). Un errore qui
        # (bridge o disco) non deve MAI fermare il compito in corso, stesso principio del
        # checkpoint sopra.
        try:
            self.task_bridge.track_progress(outcome, session_id=current_session_id(), device_id=current_device_id())
            self.task_monitor_store.save(self.task_monitor)
        except Exception:
            self.logger.exception("Errore aggiornando il task monitor del compito in corso")

    def _on_skill_installed(self, draft) -> None:
        # F1: always_confirm_intents/require_auth_intents (vedi sopra) sono popolati una sola
        # volta in __init__, leggendo self.skill_registry.skills COM'ERA in quel momento - una
        # skill installata piu' tardi dalla Skill Forge non ci finiva mai dentro. risk_of()
        # ricade su ADMIN per un intent non censito in core/risk.py (vedi il modulo), quindi
        # needs_central_confirmation()/needs_central_auth() sarebbero comunque vere per lei -
        # ma senza questo aggiornamento _resolve_and_execute non lo saprebbe mai ed eseguirebbe
        # la skill appena creata (codice scritto da un modello, non rivisto da un umano) SENZA
        # alcuna conferma ne' autenticazione al primo utilizzo: esattamente il tipo di buco che
        # il censimento del rischio dovrebbe rendere impossibile. Scoperto rileggendo il ciclo
        # di vita di una skill forgiata, non da un test che falliva. Stesso metodo usato per il
        # censimento iniziale in __init__ (core/policy_engine.py, PolicyEngine.sync_with_registry):
        # un solo posto invece di due copie della stessa logica che potrebbero divergere.
        self.policy_engine.register_intent(draft.intent)
        self.retriever.refresh()
        for example in draft.examples:
            try:
                self.learning.teach(self.normalizer.normalize(example), draft.intent, {}, source="forge")
            except Exception:
                self.logger.exception("Errore registrando gli esempi della skill %s", draft.intent)

    def _run_agent(self, request: str, remember_text: str | None = None) -> str:
        """Richiesta composta o non riconosciuta: l'orchestratore (v5.0, core/orchestrator.py)
        sceglie l'agente generico o uno specializzato (coding/ricerca), che pensa un passo alla
        volta e guarda i risultati veri prima di decidere il successivo (core/agent.py), invece
        di eseguire un piano fisso scritto in anticipo. Se il modello non e' raggiungibile o non
        conclude nulla, ripiega sul vecchio planner a piano fisso; se fallisce anche quello, NO_PLAN."""
        remember_text = remember_text if remember_text is not None else request
        trace_id = new_trace_id()
        try:
            outcome = self.orchestrator.run(
                request, history=self.conversation_state.get_short_term_history(),
                trace_id=trace_id, private=self.private_mode,
            )
        except Exception:
            self.logger.exception("Errore nell'agente per: %s", request)
            outcome = None

        if outcome is None or (outcome.error is not None and not outcome.did_something):
            return self._try_plan(request)

        self._publish_effect_proof_events(
            [(step.intent, step.verified) for step in outcome.steps if step.verified is not None],
            [step.intent for step in outcome.rolled_back],
            trace_id=trace_id,
        )

        if outcome.pending_confirmation is not None:
            reason = "auth_required" if outcome.pending_confirmation.get("kind") == "AUTH_REQUIRED" else "confirmation_required"
            # F1.5.4 ("mostrare all'utente la sorgente che ha suggerito un'azione sensibile"):
            # None quando il passo precedente non ha restituito contenuto esterno (il caso
            # comune, vedi core/agent.py::TaskAgent.run()) - non aggiunto alla busta di conferma
            # ne' al messaggio in quel caso, per non introdurre rumore su ogni conferma ordinaria.
            external_source = outcome.pending_confirmation.get("suggested_by_external_content")
            self.conversation_state.set_pending_action({
                "intent": outcome.pending_confirmation["intent"],
                "parameters": outcome.pending_confirmation["parameters"],
                "reason": reason,
                "text": remember_text,
                # F1: ripreso da _finalize_pending_action per far comparire la ricevuta
                # dell'esecuzione vera, dopo la conferma, correlata alla stessa richiesta invece
                # di un trace_id scollegato - vedi TaskAgent._log_step per il trace_id dei passi
                # dell'agente che hanno gia' portato a questa richiesta di conferma.
                "trace_id": trace_id,
                "policy_reason": outcome.pending_confirmation.get("policy_reason"),
                "suggested_by_external_content": external_source,
            })
            message = outcome.pending_confirmation["message"]
            if external_source is not None:
                message = f"{message} (Attenzione: suggerito da contenuto esterno - {external_source})"
            # F6.3/F6.7: l'evento IMPORTANTE che questo incremento collega - una conferma/
            # autenticazione richiesta a meta' di un compito composto. Il ponte valuta urgenza e
            # contesto (rischio dell'intent, modalita' corrente, quiet hours) e pubblica su
            # event_bus task/session/device id, le azioni GIA' eseguite e questa stessa decisione
            # - la sua Decision non cambia il messaggio testuale (gia' deciso sopra), solo se/come
            # Jake segnala l'evento su un canale esterno (HUD/companion).
            pending = outcome.pending_confirmation
            self._publish_task_event(lambda: self.task_bridge.decision_required(
                outcome, message=message, intent=pending["intent"], parameters=pending["parameters"],
                policy_reason=pending.get("policy_reason"),
                session_id=current_session_id(), device_id=current_device_id(),
            ))
            self._remember_exchange(remember_text, Command("AGENT", {"request": request}), message)
            return message

        if outcome.question is not None:
            # F1.8.4 ("checkpoint"): il compito NON e' interrotto anomalamente - l'agente ha
            # chiesto qualcosa e _continue_agent() (sotto) ripartira' da capo con la risposta,
            # costruendo un checkpoint nuovo se necessario. Il checkpoint di QUESTO tentativo non
            # serve piu'.
            self.agent_checkpoints.clear()
            self.conversation_state.set_pending_action({
                "intent": "AGENT_CONTINUE",
                "parameters": {"request": request, "question": outcome.question},
                "reason": "agent_question",
                "text": remember_text,
                # F6.3/F6.7: cosi' _continue_agent puo' chiudere il compito che il ponte stava
                # seguendo quando l'utente risponde (vedi resolve_decision li' sotto) - nessun
                # altro codice esistente leggeva "trace_id" per un'azione "agent_question" prima
                # di questo incremento, aggiungerlo non cambia alcun comportamento gia' esistente.
                "trace_id": outcome.trace_id,
            })
            # F6.3/F6.7: anche una domanda di chiarimento e' una decisione richiesta all'utente a
            # meta' di un compito (intent=None: nessun rischio da un'azione specifica da
            # valutare, mai critica per default) - stessa valutazione/stesso evento contestuale
            # della conferma sopra, non un percorso separato.
            self._publish_task_event(lambda: self.task_bridge.decision_required(
                outcome, message=outcome.question, session_id=current_session_id(), device_id=current_device_id(),
            ))
            self._remember_exchange(remember_text, Command("AGENT", {"request": request}), outcome.question)
            return outcome.question

        # F1.8.4 ("checkpoint"): il compito e' CONCLUSO (risposta finale o nessun piano) - un
        # checkpoint serve solo per un'interruzione ANOMALA a meta', mai per il normale "e'
        # finito" (altrimenti una futura RESUME_INTERRUPTED_TASK crederebbe che ci sia ancora
        # qualcosa da riprendere quando in realta' il compito precedente e' semplicemente finito).
        self.agent_checkpoints.clear()
        response = outcome.final_answer or self.NO_PLAN
        # F6.7.2: chiude il compito (nessun effetto se il ponte non lo aveva mai aperto - un
        # turno che non ha eseguito alcun passo dell'agente, il caso comune di una risposta
        # breve). `outcome.error` distingue un compito finito con un errore interno da uno
        # concluso normalmente, senza alcuna nuova logica: il campo esiste gia'.
        self._publish_task_event(lambda: self.task_bridge.finish_task(
            outcome, message=response, success=outcome.error is None,
            session_id=current_session_id(), device_id=current_device_id(),
        ))
        self._remember_exchange(remember_text, Command("AGENT", {"request": request}), response)
        return response

    def _continue_agent(self, action: dict, answer_text: str) -> str:
        """L'utente ha risposto alla domanda di chiarimento posta dall'agente: si riprende il
        compito con la richiesta originale piu' la risposta appena data.

        F6.3/F6.7: il compito che il ponte stava seguendo (decision_required per la domanda
        stessa, vedi _run_agent) si chiude QUI - la risposta lo risolve, anche se il compito
        VERO continua sotto un trace_id nuovo (_run_agent ne genera sempre uno fresco): sono due
        esecuzioni distinte per il ledger/il checkpoint (mai state la stessa), lo sono anche per
        il monitor."""
        self._publish_task_event(lambda: self.task_bridge.resolve_decision(action.get("trace_id"), message=answer_text, success=True))
        request = action["parameters"]["request"]
        question = action["parameters"].get("question", "")
        combined = f"{request}\n(L'utente ha risposto alla domanda \"{question}\" con: {answer_text})"
        return self._run_agent(combined, remember_text=answer_text)

    def _resume_interrupted_task(self, checkpoint: AgentCheckpoint) -> str:
        """F1.8.4 ("checkpoint... da cui riprendere"): non serve modificare TaskAgent.run() per
        "riprendere davvero" un compito - il modello vede gia' cosa e' stato fatto (riassunto
        dentro la richiesta stessa) e decide da solo il prossimo passo, esattamente come farebbe
        per qualunque altra richiesta. Se `_run_agent()` sceglie di nuovo l'agente "general", un
        checkpoint NUOVO sostituisce naturalmente questo (stesso meccanismo di on_step_completed,
        vedi __init__) - nessuna pulizia esplicita necessaria qui."""
        steps_summary = "; ".join(
            f"{step['intent']}({step['parameters']}) -> {'riuscito' if step['success'] else 'fallito'}"
            for step in checkpoint.completed_steps
        ) or "nessun passo ancora completato"
        resume_request = (
            f"{checkpoint.request}\n\n(Questo compito era gia' iniziato e poi interrotto prima di "
            f"finire, senza colpa dell'utente. Passi gia' completati: {steps_summary}. Continua da "
            f"dove eri rimasto, senza ripetere questi passi se non e' necessario.)"
        )
        return self._run_agent(resume_request, remember_text="riprendi il compito interrotto")
