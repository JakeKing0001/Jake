import time
from dataclasses import dataclass, field

from core.action_contracts import ActionError, validate_action_error
from core.action_ledger import (
    ActionLedger, ActionReceipt, authorization_of, idempotency_key_of, new_action_id,
    verification_status_of,
)
from core.execution_safety import VERIFIABLE_INTENTS, execute_with_retry, rollback_effect, verify_effect
from core.identity import current_windows_user
from core.kill_switch import KillSwitch
from core.logger import log_action, new_trace_id
from core.planner import PlanStep
from core.policy_engine import PolicyDecision, strip_authorization_signals
from core.request_context import current_device_id
from core.risk import risk_of
from core.session_recorder import SessionRecorder
from core.skill_result import SkillResult


@dataclass
class StepOutcome:
    step: PlanStep
    result: SkillResult
    attempts: int
    rolled_back: bool = False
    # F1.3.8 ("esporre... prove a HUD/companion"): stesso tri-stato gia' calcolato per il ledger
    # (verification_status_of()), None quando l'intent non ha un verificatore indipendente -
    # stessa semantica di AgentStep.verified (core/agent.py), vedi li' per il perche' non usa
    # sempre uno dei tre valori espliciti come fa invece ActionReceipt.verified.
    verified: str | None = None


@dataclass
class PlanOutcome:
    completed: list[StepOutcome] = field(default_factory=list)
    stopped_step: StepOutcome | None = None
    rolled_back: list[StepOutcome] = field(default_factory=list)
    # F1.7.2 ("collegare command, sub-step, verifica, undo e notifica con lo stesso trace id"):
    # buco reale - execute() gia' correla ogni singolo passo alla stessa ricevuta nel ledger
    # tramite trace_id (vedi _log_step), ma l'OUTCOME restituito al chiamante non lo portava mai
    # con se'. Per un piano lanciato da un comando diretto questo non si notava (l'intera
    # richiesta resta nello stesso turno, gia' correlato altrove), ma per TriggerScheduler - che
    # fa partire un piano DA SOLO, in background, senza alcun turno di conversazione a cui
    # agganciarsi - la notifica finale ("Ho eseguito automaticamente 'X'") non aveva NESSUN modo
    # di essere ricollegata alle ricevute nel ledger che quella stessa esecuzione ha prodotto.
    # Stringa vuota (non None) come default: un trace_id vuoto e' visibilmente "non impostato"
    # invece di richiedere un controllo None ovunque venga letto.
    trace_id: str = ""

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
        self, plan, policy_engine=None,
        trace_id: str | None = None, private: bool = False, model: str | None = None, requested_by: str = "user",
        dry_run: bool = False,
    ) -> PlanOutcome:
        """policy_engine (core/policy_engine.py::PolicyEngine) e' opzionale ma FAIL-CLOSED
        (F1.2.1, 12/09/2026): senza un policy_engine reale, OGNI passo si ferma con
        POLICY_BLOCKED invece di eseguire. Prima di questa correzione `None` significava
        "nessun controllo" (ALLOW per qualunque intent) - un default pericoloso perche' un
        futuro chiamante di questo metodo che dimenticasse di passare policy_engine (un piano
        automatico, per definizione senza nessuno pronto a confermare in tempo reale)
        eseguirebbe SILENZIOSAMENTE senza alcun gate, esattamente il rischio gia' documentato
        in docs/action-execution-paths.md ("Nota sul percorso 3"). I tre chiamanti reali
        (JakeCore._try_plan, RunWorkflowSkill dopo che JakeCore li' assegna, TriggerScheduler)
        passano gia' tutti self.policy_engine esplicitamente, quindi questo non cambia il loro
        comportamento - cambia solo l'esito di un chiamante che se ne dimenticasse, da "esegue
        tutto" a "si ferma su ogni passo", coerente con "minimo privilegio"/"negare per default"
        (ROADMAP_EXECUTION.md, F1.2.4). Un passo che la policy classifica CONFIRM va trattato
        come se richiedesse conferma (il piano si mette in pausa su quel passo, senza
        eseguirlo: nessuno e' pronto a rispondere "confermi?" in un percorso automatico), e uno
        BLOCK va bloccato allo stesso modo di un fallimento.

        F1: prima era blocked_intents/always_confirm_intents, due insiemi separati passati a
        mano - esattamente la frammentazione che ha causato il bug di RunWorkflowSkill (che ne
        riceveva solo due su tre, dimenticando i risultati, prima ancora che questo refactor
        unificasse tutto in un riferimento solo). Un chiamante ora ha UN riferimento da passare,
        non piu' due sincronizzati a mano.

        trace_id/private/model (F0, log strutturati): se nessuno li passa (es. i test esistenti,
        o un chiamante che non se ne cura ancora) se ne genera uno locale, cosi' i passi restano
        comunque correlati tra loro anche senza collegamento a una richiesta piu' ampia.
        requested_by (F1, action ledger): "user" di default (il ripiego di JakeCore._try_plan,
        sempre partito da una richiesta diretta), "trigger:<nome>" quando e' TriggerScheduler a
        far partire un'automazione da sola.

        dry_run (F6, "mostrami prima" - vedi ROADMAP.md): quando vero, NESSUNA skill viene
        davvero eseguita - ogni passo che la policy lascerebbe passare (ALLOW) viene solo
        annotato come simulato in outcome.completed, cosi' l'utente vede l'intera sequenza che
        girerebbe per davvero PRIMA di attivarla (lo scenario "quando esco, spegni tutto tranne
        il server: mostrami cosa faresti, poi testala"). Un passo che la policy fermerebbe
        comunque (BLOCK/CONFIRM) si ferma anche qui, senza differenze: il dry-run mostra la
        sequenza REALE che accadrebbe, non una finta in cui tutto va sempre bene. Nessun
        log_action/ricevuta nel ledger per un passo simulato: non e' mai successo per davvero."""
        trace_id = trace_id or new_trace_id()
        outcome = PlanOutcome(trace_id=trace_id)
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
                if not dry_run:
                    outcome.rolled_back = self._rollback(
                        outcome.completed, policy_engine, trace_id=trace_id, requested_by=requested_by, private=private,
                    )
                    self._log_step(
                        trace_id, private, model, requested_by, time.monotonic(), step.intent, safe_parameters,
                        result="error:KILLED", verified=None, policy_reason=None,
                    )
                return outcome
            # F1 (core/policy_engine.py): stessa decisione usata dal percorso interattivo di
            # JakeCore (decide_interactive), nella sua variante senza REQUIRE_AUTH - qui nessuno
            # e' pronto a rispondere "confermi?" in tempo reale, quindi un intent DESTRUCTIVE/
            # ADMIN si ferma sempre, un intent bloccato dall'utente in config.json pure.
            # F1.2.1: policy_engine=None e' FAIL-CLOSED (BLOCK), non piu' ALLOW - vedi il
            # docstring di execute() sul perche'. F1.2.6: *_with_reason() invece di
            # decide_automated() - stessa decisione, ma porta anche la motivazione (un
            # vocabolario chiuso di quattro costanti, mai testo libero - vedi
            # core/policy_engine.py::POLICY_REASONS) da salvare nel ledger. F1.2.2 (seconda
            # fetta): safe_parameters (gia' calcolato sopra, gia' senza segnali di
            # autorizzazione) passato qui cosi' la capability allowed_filesystem_roots si applica
            # anche a un piano automatico, non solo a un comando diretto.
            if policy_engine is not None:
                decision, policy_reason = policy_engine.decide_automated_with_reason(step.intent, safe_parameters)
            else:
                decision, policy_reason = PolicyDecision.BLOCK, None
            if decision == PolicyDecision.BLOCK:
                outcome.stopped_step = StepOutcome(
                    step=step,
                    result=SkillResult(success=False, data={}, error="POLICY_BLOCKED"),
                    attempts=0,
                )
                if not dry_run:
                    outcome.rolled_back = self._rollback(
                        outcome.completed, policy_engine, trace_id=trace_id, requested_by=requested_by, private=private,
                    )
                    self._log_step(
                        trace_id, private, model, requested_by, time.monotonic(), step.intent, safe_parameters,
                        result="policy_blocked", verified=None, policy_reason=policy_reason,
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
                if not dry_run:
                    self._log_step(
                        trace_id, private, model, requested_by, time.monotonic(), step.intent, safe_parameters,
                        result="confirmation_required", verified=None, policy_reason=policy_reason,
                    )
                return outcome

            if dry_run:
                outcome.completed.append(StepOutcome(
                    step=step,
                    result=SkillResult(success=True, data={"dry_run": True, "intent": step.intent, "parameters": safe_parameters}),
                    attempts=0,
                ))
                continue

            step_started = time.monotonic()
            step_outcome = self._execute_step(step, safe_parameters, policy_engine)
            verified = None
            if step_outcome.result.success:
                effect_confirmed = verify_effect(step.intent, step_outcome.result.data)
                if step.intent in VERIFIABLE_INTENTS:
                    verified = effect_confirmed
                if not effect_confirmed:
                    step_outcome.result = SkillResult(
                        success=False, data=step_outcome.result.data, error="VERIFICATION_FAILED"
                    )
            # F1.3.8: stesso principio di core/agent.py::AgentStep.verified - None quando
            # l'intent non ha un verificatore indipendente (niente da esporre alla HUD).
            step_outcome.verified = verification_status_of(verified) if verified is not None else None

            if step_outcome.result.success:
                outcome.completed.append(step_outcome)
                self._log_step(
                    trace_id, private, model, requested_by, step_started, step.intent, safe_parameters,
                    result="success", verified=verified, policy_reason=policy_reason,
                )
                continue

            outcome.stopped_step = step_outcome
            if step_outcome.result.error != "CONFIRMATION_REQUIRED":
                outcome.rolled_back = self._rollback(
                    outcome.completed, policy_engine, trace_id=trace_id, requested_by=requested_by, private=private,
                )
            self._log_step(
                trace_id, private, model, requested_by, step_started, step.intent, safe_parameters,
                result=f"error:{step_outcome.result.error}", verified=verified, policy_reason=policy_reason,
            )
            return outcome

        return outcome

    # Vedi JakeCore._NOT_A_FAILURE (core/jake_core.py): stesso criterio, non duplicato per caso.
    _NOT_A_FAILURE = {"confirmation_required", "auth_required"}

    def _log_step(
        self, trace_id: str, private: bool, model: str | None, requested_by: str, started: float, intent: str,
        parameters: dict, *, result: str, verified: bool | None, policy_reason: str | None,
    ) -> None:
        """Stesso formato e stesso trace_id condiviso di TaskAgent._log_step (core/agent.py):
        un piano fisso eseguito da PlanExecutor (il ripiego di JakeCore._try_plan, o
        un'automazione di TriggerScheduler) produce record identici a quelli dell'agente a
        passi, cosi' jake_actions.jsonl non distingue i due esecutori per chi lo legge dopo.
        Un passo fallito alimenta anche session_recorder, coi parametri del passo, per
        tools/replay_session.py. Alimenta anche action_ledger (F1) con lo stesso requested_by
        di tutto il piano. policy_reason (F1.2.6): None per il passo interrotto dal kill switch
        (non e' una decisione di policy), altrimenti una delle quattro costanti di
        core.policy_engine.POLICY_REASONS."""
        duration_ms = (time.monotonic() - started) * 1000
        risk = risk_of(intent).value
        log_action(
            trace_id, private=private, duration_ms=duration_ms,
            model=model, skill=intent, risk_decision=risk, result=result, verified=verified,
        )
        # F1.1.7 (terzo chokepoint adottato, dopo JakeCore - F1.1.6 - e TaskAgent sopra):
        # ActionError.from_result() sostituisce la chiamata diretta a error_category_of(), stesso
        # valore per receipt.error_category, nessun cambio di comportamento. Con questo, tutti e
        # tre i chokepoint reali (comando diretto/ripresa conferma, agente, piano) costruiscono lo
        # stesso tipo condiviso invece che due su tre restare su una stringa grezza.
        action_error = ActionError.from_result(result)
        validate_action_error(action_error)
        self.action_ledger.record(
            ActionReceipt(
                action_id=new_action_id(), trace_id=trace_id, ts=time.time(), intent=intent,
                requested_by=requested_by, risk_decision=risk, authorization=authorization_of(result, parameters),
                result=result, idempotency_key=idempotency_key_of(intent, parameters),
                verified=verification_status_of(verified), error_category=action_error.category,
                policy_reason=policy_reason, duration_ms=duration_ms, model=model,
                device_id=current_device_id(), windows_user=current_windows_user(),
            ),
            private=private,
        )
        if not result.startswith("success") and result not in self._NOT_A_FAILURE:
            self.session_recorder.record_failure(
                trace_id, intent=intent, parameters=parameters, error=result,
                risk_decision=risk, private=private,
            )

    def _execute_step(self, step, parameters: dict | None = None, policy_engine=None) -> StepOutcome:
        """parameters e' quello che va davvero eseguito (sanificato da execute(), vedi sopra);
        step.parameters resta quello originale del piano solo per riferimento/descrizione -
        StepOutcome.step lo conserva per format_plan_outcome, non per essere rieseguito.

        F1.2.1 (percorso 7): policy_engine e' lo stesso gia' verificato ALLOW poco sopra in
        execute() - non un secondo controllo diverso, solo rifornito a SkillRegistry.execute()
        (ora fail-closed di default sul proprio blocked_intents) perche' non si blocchi da solo su
        un passo gia' approvato."""
        parameters = step.parameters if parameters is None else parameters
        result, attempts = execute_with_retry(
            lambda intent, params: self.skill_registry.execute(intent, params, policy_engine=policy_engine),
            step.intent, parameters,
        )
        return StepOutcome(step=step, result=result, attempts=attempts)

    def _rollback(
        self, completed_steps: list, policy_engine=None, *,
        trace_id: str | None = None, requested_by: str = "user", private: bool = False,
    ) -> list:
        rolled_back = []
        for step_outcome in reversed(completed_steps):
            if rollback_effect(
                self.skill_registry, step_outcome.step.intent, step_outcome.result.data,
                policy_engine=policy_engine,
                action_ledger=self.action_ledger, trace_id=trace_id, requested_by=requested_by, private=private,
            ):
                step_outcome.rolled_back = True
                rolled_back.append(step_outcome)
        return rolled_back
