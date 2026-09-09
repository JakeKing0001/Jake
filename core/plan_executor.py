import time
from dataclasses import dataclass, field

from core.action_ledger import ActionLedger, ActionReceipt, authorization_of, idempotency_key_of, new_action_id
from core.execution_safety import VERIFIABLE_INTENTS, execute_with_retry, rollback_effect, verify_effect
from core.kill_switch import KillSwitch
from core.logger import log_action, new_trace_id
from core.planner import PlanStep
from core.policy_engine import PolicyDecision, decide_automated, strip_authorization_signals
from core.risk import risk_of
from core.session_recorder import SessionRecorder
from core.skill_result import SkillResult


@dataclass
class StepOutcome:
    step: PlanStep
    result: SkillResult
    attempts: int
    rolled_back: bool = False


@dataclass
class PlanOutcome:
    completed: list[StepOutcome] = field(default_factory=list)
    stopped_step: StepOutcome = None
    rolled_back: list[StepOutcome] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.stopped_step is None


class PlanExecutor:
    """Esegue un piano passo dopo passo: retry sugli errori transitori, rollback sul fallimento
    (vedi core/execution_safety.py, condiviso con l'agente a passi in core/agent.py).

    Un passo che richiede conferma (operazione rischiosa) mette in pausa il piano senza
    eseguirlo: la sicurezza delle conferme non viene mai aggirata da una richiesta multi-step."""

    def __init__(self, skill_registry, session_recorder=None, action_ledger=None, kill_switch=None):
        self.skill_registry = skill_registry
        # Disattivato per default (vedi SessionRecorder.__init__) finche' JakeCore non assegna
        # il proprio, condiviso con _execute_command e TaskAgent (vedi core/jake_core.py): senza,
        # e' un no-op, non un errore - PlanExecutor e' costruito da SkillRegistry, prima che
        # JakeCore possa passargliene uno alla creazione.
        self.session_recorder = session_recorder or SessionRecorder()
        # F1: come session_recorder, condiviso se passato, altrimenti un'istanza locale che
        # scrive comunque (il ledger e' sempre attivo, non opt-in).
        self.action_ledger = action_ledger or ActionLedger()
        # F1: come sopra - condiviso con TaskAgent/JakeCore se passato (un solo interruttore per
        # tutto, vedi core/kill_switch.py), altrimenti un'istanza locale mai attivata.
        self.kill_switch = kill_switch or KillSwitch()

    def execute(
        self, plan, blocked_intents: set = None, always_confirm_intents: set = None,
        trace_id: str = None, private: bool = False, model: str = None, requested_by: str = "user",
    ) -> PlanOutcome:
        """blocked_intents/always_confirm_intents sono opzionali (default None = nessun
        controllo, comportamento identico a prima) perche' oggi solo JakeCore._process()
        applica questa policy sui comandi singoli: RUN_WORKFLOW la aggirava del tutto.
        Un trigger che fa partire un'automazione da solo (v3.0), pero', non ha nessuno li'
        pronto a rispondere "confermi?": un passo in always_confirm_intents va quindi trattato
        come se richiedesse conferma (il piano si mette in pausa su quel passo, senza
        eseguirlo), e uno in blocked_intents va bloccato allo stesso modo di un fallimento.

        trace_id/private/model (F0, log strutturati): se nessuno li passa (es. i test esistenti,
        o un chiamante che non se ne cura ancora) se ne genera uno locale, cosi' i passi restano
        comunque correlati tra loro anche senza collegamento a una richiesta piu' ampia.
        requested_by (F1, action ledger): "user" di default (il ripiego di JakeCore._try_plan,
        sempre partito da una richiesta diretta), "trigger:<nome>" quando e' TriggerScheduler a
        far partire un'automazione da sola."""
        trace_id = trace_id or new_trace_id()
        outcome = PlanOutcome()
        for step in plan.steps:
            # F1: mai i parametri originali del passo da qui in poi (esecuzione E logging) - vedi
            # core/policy_engine.py sul perche' un piano automatico non puo' mai arrivare gia'
            # "confirmed"/"authenticated".
            safe_parameters = strip_authorization_signals(step.parameters)
            if self.kill_switch.is_active():
                # F1: controllato SOLO tra un passo e il successivo (vedi core/kill_switch.py).
                outcome.stopped_step = StepOutcome(
                    step=step, result=SkillResult(success=False, data={}, error="KILLED"), attempts=0,
                )
                outcome.rolled_back = self._rollback(outcome.completed)
                self._log_step(
                    trace_id, private, model, requested_by, time.monotonic(), step.intent, safe_parameters,
                    result="error:KILLED", verified=None,
                )
                return outcome
            # F1 (core/policy_engine.py): stessa decisione usata dal percorso interattivo di
            # JakeCore (decide_interactive), nella sua variante senza REQUIRE_AUTH - qui nessuno
            # e' pronto a rispondere "confermi?" in tempo reale, quindi un intent DESTRUCTIVE/
            # ADMIN si ferma sempre, un intent bloccato dall'utente in config.json pure.
            decision = decide_automated(
                step.intent, blocked_intents=blocked_intents, always_confirm_intents=always_confirm_intents,
            )
            if decision == PolicyDecision.BLOCK:
                outcome.stopped_step = StepOutcome(
                    step=step,
                    result=SkillResult(success=False, data={}, error="POLICY_BLOCKED"),
                    attempts=0,
                )
                outcome.rolled_back = self._rollback(outcome.completed)
                self._log_step(
                    trace_id, private, model, requested_by, time.monotonic(), step.intent, safe_parameters,
                    result="policy_blocked", verified=None,
                )
                return outcome
            if decision == PolicyDecision.CONFIRM:
                outcome.stopped_step = StepOutcome(
                    step=step,
                    result=SkillResult(
                        success=False, data={"message": "Richiede conferma manuale."},
                        error="CONFIRMATION_REQUIRED",
                    ),
                    attempts=0,
                )
                self._log_step(
                    trace_id, private, model, requested_by, time.monotonic(), step.intent, safe_parameters,
                    result="confirmation_required", verified=None,
                )
                return outcome

            step_started = time.monotonic()
            step_outcome = self._execute_step(step, safe_parameters)
            verified = None
            if step_outcome.result.success:
                effect_confirmed = verify_effect(step.intent, step_outcome.result.data)
                if step.intent in VERIFIABLE_INTENTS:
                    verified = effect_confirmed
                if not effect_confirmed:
                    step_outcome.result = SkillResult(
                        success=False, data=step_outcome.result.data, error="VERIFICATION_FAILED"
                    )

            if step_outcome.result.success:
                outcome.completed.append(step_outcome)
                self._log_step(
                    trace_id, private, model, requested_by, step_started, step.intent, safe_parameters,
                    result="success", verified=verified,
                )
                continue

            outcome.stopped_step = step_outcome
            if step_outcome.result.error != "CONFIRMATION_REQUIRED":
                outcome.rolled_back = self._rollback(outcome.completed)
            self._log_step(
                trace_id, private, model, requested_by, step_started, step.intent, safe_parameters,
                result=f"error:{step_outcome.result.error}", verified=verified,
            )
            return outcome

        return outcome

    # Vedi JakeCore._NOT_A_FAILURE (core/jake_core.py): stesso criterio, non duplicato per caso.
    _NOT_A_FAILURE = {"confirmation_required", "auth_required"}

    def _log_step(
        self, trace_id: str, private: bool, model: str, requested_by: str, started: float, intent: str,
        parameters: dict, *, result: str, verified: bool | None,
    ) -> None:
        """Stesso formato e stesso trace_id condiviso di TaskAgent._log_step (core/agent.py):
        un piano fisso eseguito da PlanExecutor (il ripiego di JakeCore._try_plan, o
        un'automazione di TriggerScheduler) produce record identici a quelli dell'agente a
        passi, cosi' jake_actions.jsonl non distingue i due esecutori per chi lo legge dopo.
        Un passo fallito alimenta anche session_recorder, coi parametri del passo, per
        tools/replay_session.py. Alimenta anche action_ledger (F1) con lo stesso requested_by
        di tutto il piano."""
        duration_ms = (time.monotonic() - started) * 1000
        risk = risk_of(intent).value
        log_action(
            trace_id, private=private, duration_ms=duration_ms,
            model=model, skill=intent, risk_decision=risk, result=result, verified=verified,
        )
        self.action_ledger.record(
            ActionReceipt(
                action_id=new_action_id(), trace_id=trace_id, ts=time.time(), intent=intent,
                requested_by=requested_by, risk_decision=risk, authorization=authorization_of(result, parameters),
                result=result, idempotency_key=idempotency_key_of(intent, parameters),
                verified=verified, duration_ms=duration_ms, model=model,
            ),
            private=private,
        )
        if not result.startswith("success") and result not in self._NOT_A_FAILURE:
            self.session_recorder.record_failure(
                trace_id, intent=intent, parameters=parameters, error=result,
                risk_decision=risk, private=private,
            )

    def _execute_step(self, step, parameters: dict = None) -> StepOutcome:
        """parameters e' quello che va davvero eseguito (sanificato da execute(), vedi sopra);
        step.parameters resta quello originale del piano solo per riferimento/descrizione -
        StepOutcome.step lo conserva per format_plan_outcome, non per essere rieseguito."""
        parameters = step.parameters if parameters is None else parameters
        result, attempts = execute_with_retry(self.skill_registry.execute, step.intent, parameters)
        return StepOutcome(step=step, result=result, attempts=attempts)

    def _rollback(self, completed_steps: list) -> list:
        rolled_back = []
        for step_outcome in reversed(completed_steps):
            if rollback_effect(self.skill_registry, step_outcome.step.intent, step_outcome.result.data):
                step_outcome.rolled_back = True
                rolled_back.append(step_outcome)
        return rolled_back
