from dataclasses import dataclass, field

from core.execution_safety import execute_with_retry, rollback_effect, verify_effect
from core.skill_result import SkillResult


@dataclass
class StepOutcome:
    step: "PlanStep"
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

    def __init__(self, skill_registry):
        self.skill_registry = skill_registry

    def execute(self, plan, blocked_intents: set = None, always_confirm_intents: set = None) -> PlanOutcome:
        """blocked_intents/always_confirm_intents sono opzionali (default None = nessun
        controllo, comportamento identico a prima) perche' oggi solo JakeCore._process()
        applica questa policy sui comandi singoli: RUN_WORKFLOW la aggirava del tutto.
        Un trigger che fa partire un'automazione da solo (v3.0), pero', non ha nessuno li'
        pronto a rispondere "confermi?": un passo in always_confirm_intents va quindi trattato
        come se richiedesse conferma (il piano si mette in pausa su quel passo, senza
        eseguirlo), e uno in blocked_intents va bloccato allo stesso modo di un fallimento."""
        outcome = PlanOutcome()
        for step in plan.steps:
            if blocked_intents and step.intent in blocked_intents:
                outcome.stopped_step = StepOutcome(
                    step=step,
                    result=SkillResult(success=False, data={}, error="POLICY_BLOCKED"),
                    attempts=0,
                )
                outcome.rolled_back = self._rollback(outcome.completed)
                return outcome
            if always_confirm_intents and step.intent in always_confirm_intents:
                outcome.stopped_step = StepOutcome(
                    step=step,
                    result=SkillResult(
                        success=False, data={"message": "Richiede conferma manuale."},
                        error="CONFIRMATION_REQUIRED",
                    ),
                    attempts=0,
                )
                return outcome

            step_outcome = self._execute_step(step)
            if step_outcome.result.success and not verify_effect(step.intent, step_outcome.result.data):
                step_outcome.result = SkillResult(
                    success=False, data=step_outcome.result.data, error="VERIFICATION_FAILED"
                )

            if step_outcome.result.success:
                outcome.completed.append(step_outcome)
                continue

            outcome.stopped_step = step_outcome
            if step_outcome.result.error != "CONFIRMATION_REQUIRED":
                outcome.rolled_back = self._rollback(outcome.completed)
            return outcome

        return outcome

    def _execute_step(self, step) -> StepOutcome:
        result, attempts = execute_with_retry(self.skill_registry.execute, step.intent, step.parameters)
        return StepOutcome(step=step, result=result, attempts=attempts)

    def _rollback(self, completed_steps: list) -> list:
        rolled_back = []
        for step_outcome in reversed(completed_steps):
            if rollback_effect(self.skill_registry, step_outcome.step.intent, step_outcome.result.data):
                step_outcome.rolled_back = True
                rolled_back.append(step_outcome)
        return rolled_back
