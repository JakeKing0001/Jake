"""Ponte tra Task Monitor (F6.7) e Notification Intelligence (F6.3): il primo flusso end-to-end reale che
collega i due sistemi a JakeCore/EventBus/HUD, non due moduli costruiti e mai collegati a nulla.

Il problema che risolve: un compito composto (`core/agent.py::TaskAgent`) puo' incontrare un evento
importante A META' strada - una conferma o un'autenticazione richieste, una domanda di chiarimento - e fino a
questo incremento l'unico segnale che l'utente vedeva era il messaggio testuale della conversazione, senza
nessuna valutazione di urgenza/contesto (una conferma per cancellare un file merita la stessa attenzione di
"vuoi il meteo di domani?") e senza che un HUD o un'app companion potesse saperlo in tempo reale con il
proprio task/session id, le azioni GIA' eseguite e la decisione richiesta.

Come collega i tre pezzi (nessuna logica di dominio nuova, solo il filo):
1. `core.task_monitor.TaskMonitorRegistry` segue il compito (F6.7.1: silenzioso passo per passo, un evento
   solo alle transizioni che contano).
2. `core.notification_policy.NotificationPolicy` valuta l'evento (priorita', modalita', quiet hours,
   criticita') e decide SE/COME interrompere ORA (F6.3).
3. `core.event_bus.EventBus` porta la decisione fuori da JakeCore come un `HudEvent` NOTIFICATION reale, con
   task_id, session_id, device_id, le azioni gia' eseguite e la decisione richiesta nel payload.

JakeCore chiama solo due metodi da due punti gia' esistenti della sua pipeline (vedi la sezione "F6.3/F6.7:
Task Monitor + Notification Intelligence" in core/jake_core.py): `track_progress` da
`_on_agent_step_completed` (gia' agganciato a `TaskAgent.on_step_completed`, chiamato dopo ogni passo
riuscito), `decision_required`/`finish_task` da `_run_agent` (dove pending_confirmation/domanda/risposta
finale sono gia' gestiti).

Urgenza dichiarata, non dedotta dal contenuto: la criticita' di una decisione richiesta viene dal RISCHIO
gia' classificato dell'intent (`core/risk.py::risk_of`, la stessa tassonomia che PolicyEngine/
TaskRiskBudget usano gia' altrove in Jake), mai da un'euristica sul testo del messaggio - stesso principio
"il rischio si dichiara, non si deduce dal contenuto" gia' applicato al resto del progetto. Il chiamante puo'
sempre dichiararla esplicitamente (`critical=True/False`), che vince sempre sul rischio derivato.

Limiti dichiarati (collegare cio' che esiste, non costruire nuova superficie):
- `devices` resta opzionale (default None): JakeCore non mantiene ancora una lista di
  `core.notification_policy.Device` dai dispositivi companion accoppiati (`core/device_registry.py` traccia
  identita' e sessioni, non kind/shared_speaker/private_screen) - il canale (voce/schermo) e la soglia di
  interruzione restano comunque pienamente valutati, solo l'instradamento a UN device preciso no.
- nessuna rilevazione di anomalia (stallo di un compito): richiederebbe un timer in background che oggi non
  esiste; `TaskMonitorRegistry.flag_anomaly`/`is_stalled` restano disponibili ma non collegati qui.
- `MonitorStore.resume_candidates()` (F6.7.7) e' salvato da JakeCore (vedi `_on_agent_step_completed`) ma
  nessuna skill lo espone ancora: stesso principio gia' applicato a `core/agent_checkpoint.py` (nessuna
  ripresa automatica), aggiungere quella superficie e' fuori scopo per questo incremento."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.hud_protocol import EventType, HudEvent
from core.notification_center import NotificationMode
from core.notification_policy import Decision, Device, Notification, NotificationPolicy
from core.risk import RiskLevel, is_at_least, risk_of
from core.task_monitor import TaskMonitorRegistry, TaskStatus, UnknownTaskError

DEFAULT_LABEL_MAX_CHARS = 120


def _short_label(text: str | None) -> str:
    text = (text or "compito").strip() or "compito"
    return text if len(text) <= DEFAULT_LABEL_MAX_CHARS else text[: DEFAULT_LABEL_MAX_CHARS - 1] + "…"


@dataclass(frozen=True)
class TaskContext:
    """Session/device id (F1.2.3, `core/request_context.py`) non appartengono al monitor generico
    (riusabile anche fuori da un contesto companion): vivono qui, per esecuzione."""

    session_id: str | None = None
    device_id: str | None = None


class TaskNotificationBridge:
    def __init__(self, monitor: TaskMonitorRegistry, policy: NotificationPolicy, event_bus,
                mode_source: Callable[[], NotificationMode] | None = None) -> None:
        self.monitor = monitor
        self.policy = policy
        self.event_bus = event_bus
        # F6.3: `NotificationCenter.mode` (v4.3) resta l'UNICA fonte di verita' della modalita' corrente
        # (cambiata da SET_NOTIFICATION_MODE) - `policy.mode` viene riletta da qui ad ogni decisione invece di
        # essere una seconda copia che potrebbe disallinearsi (la stessa classe di buco gia' trovata e
        # corretta piu' volte in questa sessione per altri stati condivisi tra thread/componenti).
        self._mode_source = mode_source
        self._contexts: dict[str, TaskContext] = {}
        self._actions: dict[str, list[dict]] = {}

    def _sync_mode(self) -> None:
        if self._mode_source is not None:
            self.policy.mode = self._mode_source()

    def _ensure_started(self, task_id: str, label: str) -> None:
        try:
            self.monitor.get(task_id)
        except UnknownTaskError:
            self.monitor.start(task_id, _short_label(label))
            self._contexts[task_id] = TaskContext()
            self._actions[task_id] = []

    def _update_context(self, task_id: str, session_id: str | None, device_id: str | None) -> None:
        """Un campo passato come None NON cancella un valore gia' noto da una chiamata precedente per lo
        stesso task (es. `track_progress` senza session_id dopo che `decision_required` l'aveva gia' saputo) -
        solo un valore esplicito sovrascrive."""
        if session_id is None and device_id is None:
            return
        current = self._contexts.get(task_id, TaskContext())
        self._contexts[task_id] = TaskContext(
            session_id if session_id is not None else current.session_id,
            device_id if device_id is not None else current.device_id,
        )

    @staticmethod
    def _steps_to_actions(steps) -> list[dict]:
        """Solo intent ed esito (mai l'intero `SkillResult`): stesso principio gia' applicato al checkpoint
        dell'agente (`core/jake_core.py::_on_agent_step_completed`) - i dati grezzi di una skill possono
        contenere contenuto esterno/sensibile che non ha senso duplicare su un evento del bus."""
        return [{"intent": step.intent, "success": bool(step.result and step.result.success)} for step in steps]

    def track_progress(self, outcome, *, session_id: str | None = None, device_id: str | None = None) -> None:
        """F6.7.1: da chiamare dopo OGNI passo riuscito/fallito dell'agente - aggiorna silenziosamente il
        monitor con le azioni fatte finora, nessun evento pubblicato (il "silenzio durante il progresso" che
        F6.7.1 chiede)."""
        if outcome.trace_id is None:
            return
        self._ensure_started(outcome.trace_id, outcome.request or "")
        self._update_context(outcome.trace_id, session_id, device_id)
        self._actions[outcome.trace_id] = self._steps_to_actions(outcome.steps)
        task = self.monitor.get(outcome.trace_id)
        if task.status == TaskStatus.RUNNING:  # difensivo: in questo flusso e' sempre vero, vedi il docstring
            last = outcome.steps[-1] if outcome.steps else None
            self.monitor.progress(outcome.trace_id, last.intent if last else "")

    def _publish(self, task_id: str, status: TaskStatus, label: str, message: str, *, decision: Decision,
                decision_required: dict | None) -> None:
        context = self._contexts.get(task_id, TaskContext())
        payload = {
            "origin": "task_monitor", "task_id": task_id, "session_id": context.session_id,
            "device_id": context.device_id, "label": label, "status": status.value, "message": message,
            "actions_done": self._actions.get(task_id, []),
            "decision_required": decision_required,
            "decision": {
                "action": decision.action, "reason": decision.reason, "priority": decision.priority,
                "channel": decision.channel, "device_id": decision.device_id,
                "spoken_text": decision.spoken_text, "screen_text": decision.screen_text,
            },
        }
        self.event_bus.publish(HudEvent(EventType.NOTIFICATION, payload, trace_id=task_id))

    def decision_required(self, outcome, *, message: str, intent: str | None = None, parameters: dict | None = None,
                          policy_reason: str | None = None, critical: bool | None = None, sensitivity: str = "unknown",
                          contact: str | None = None, devices: list[Device] | None = None,
                          session_id: str | None = None, device_id: str | None = None) -> Decision:
        """F6.7.2/F6.3: un evento IMPORTANTE durante il compito - una conferma/autenticazione richiesta
        (`intent` valorizzato) o una domanda di chiarimento dell'agente (`intent=None`). Valuta urgenza e
        contesto con `NotificationPolicy`, pubblica il contesto completo sul bus (task/session/device id,
        azioni gia' eseguite, decisione richiesta) e ritorna la `Decision` cosi' il chiamante sa se/come Jake
        ha deciso di interrompere ORA."""
        task_id = outcome.trace_id
        self._ensure_started(task_id, outcome.request or message)
        self._update_context(task_id, session_id, device_id)
        self._actions[task_id] = self._steps_to_actions(outcome.steps)
        label = self.monitor.get(task_id).label
        if critical is None:
            critical = intent is not None and is_at_least(risk_of(intent), RiskLevel.DESTRUCTIVE)
        self._sync_mode()
        notification = Notification(kind="decision_required", message=message, source=intent or "clarifying_question",
                                     critical=critical, sensitivity=sensitivity, contact=contact)
        decision = self.policy.decide(notification, devices)
        self.monitor.require_decision(task_id, message)
        self.monitor.drain_events()  # bookkeeping interno del monitor: il ponte pubblica il SUO evento sotto
        self._publish(task_id, TaskStatus.NEEDS_DECISION, label, message, decision=decision,
                      decision_required={"intent": intent, "parameters": parameters, "message": message,
                                        "policy_reason": policy_reason})
        return decision

    def finish_task(self, outcome, *, message: str = "", success: bool = True,
                    session_id: str | None = None, device_id: str | None = None) -> Decision | None:
        """F6.7.2/F6.7.7: chiude un compito CONCLUSO SENZA MAI aver chiesto una decisione (risposta finale o
        errore diretto - vedi `resolve_decision` per il caso in cui una decisione E' stata chiesta e ORA si
        risolve). None se nessun monitor era mai stato aperto per questa esecuzione (nessun passo e' mai
        arrivato a `track_progress` - il caso normale di un turno che non ha mai eseguito un passo
        dell'agente) o se il compito e' gia' concluso o in attesa di una decisione (non tocca a QUESTO
        metodo chiuderlo in quel caso)."""
        task_id = outcome.trace_id
        if task_id is None:
            return None
        try:
            task = self.monitor.get(task_id)
        except UnknownTaskError:
            return None
        if task.status not in (TaskStatus.RUNNING, TaskStatus.ANOMALY):
            return None
        self._update_context(task_id, session_id, device_id)
        self._actions[task_id] = self._steps_to_actions(outcome.steps)
        return self._close(task_id, task.label, message, success, source=outcome.agent_name or "")

    def resolve_decision(self, task_id: str | None, *, message: str = "", success: bool = True) -> Decision | None:
        """F6.7.2: chiude un compito la cui decisione era stata chiesta con `decision_required` e ORA e' stata
        risolta FUORI da un nuovo `AgentOutcome` - una conferma testuale ordinaria ("si'"/"no", anche da
        `POST /approvals/<task_id>`), non un nuovo giro dell'agente (vedi `core/jake_core.py::
        _finalize_pending_action`/`_handle_confirmation`). A differenza di `finish_task`, non ha un
        `AgentOutcome` da cui aggiornare le azioni gia' fatte: usa quelle gia' note al compito dall'ultimo
        `track_progress`/`decision_required`. None se il compito non era mai stato tracciato (task_id
        assente/sconosciuto - il caso normale di una conferma che non passa mai da `decision_required`, es.
        DELETE_PATH confermato da un comando diretto) o e' gia' concluso."""
        if task_id is None:
            return None
        try:
            task = self.monitor.get(task_id)
        except UnknownTaskError:
            return None
        if task.status not in (TaskStatus.RUNNING, TaskStatus.ANOMALY, TaskStatus.NEEDS_DECISION):
            return None
        return self._close(task_id, task.label, message, success)

    def _close(self, task_id: str, label: str, message: str, success: bool, *, source: str = "") -> Decision:
        self._sync_mode()
        kind = "task_completed" if success else "task_error"
        notification = Notification(kind=kind, message=message or ("completato" if success else "errore"),
                                     source=source, critical=False)
        decision = self.policy.decide(notification, None)
        if success:
            self.monitor.complete(task_id, message or "completato")
            status = TaskStatus.COMPLETED
        else:
            self.monitor.fail(task_id, message or "errore")
            status = TaskStatus.ERROR
        self.monitor.drain_events()
        self._publish(task_id, status, label, message, decision=decision, decision_required=None)
        self.monitor.close(task_id)
        self._contexts.pop(task_id, None)
        self._actions.pop(task_id, None)
        return decision
