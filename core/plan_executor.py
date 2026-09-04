from dataclasses import dataclass, field
from pathlib import Path

from core.skill_result import SkillResult


def _rollback_create_path(registry, step, result):
    registry.execute("DELETE_PATH", {"path": result.data["path"], "confirmed": True})


def _rollback_rename_path(registry, step, result):
    original_name = Path(result.data["path"]).name
    registry.execute("RENAME_PATH", {"path": result.data["new_path"], "new_name": original_name})


def _rollback_move_path(registry, step, result):
    original_dir = str(Path(result.data["path"]).parent)
    registry.execute("MOVE_PATH", {
        "path": result.data["new_path"], "destination": original_dir, "confirmed": True,
    })


# Solo le operazioni filesystem con un inverso naturale sono annullabili: le altre (aprire
# un'app, cercare, ricordare un'informazione, ...) non hanno un rollback sensato e vengono
# lasciate cosi' come sono, come previsto dalla roadmap ("rollback dove possibile").
ROLLBACK_HANDLERS = {
    "CREATE_PATH": _rollback_create_path,
    "RENAME_PATH": _rollback_rename_path,
    "MOVE_PATH": _rollback_move_path,
}


def _verify_step(step_outcome) -> bool:
    """Verificatore indipendente (v1.5): controlla che l'effetto dichiarato dalla skill sia
    davvero avvenuto sul filesystem, invece di fidarsi ciecamente del 'success' riportato."""
    intent = step_outcome.step.intent
    data = step_outcome.result.data

    if intent == "CREATE_PATH":
        return Path(data["path"]).exists()
    if intent in ("RENAME_PATH", "MOVE_PATH"):
        return Path(data["new_path"]).exists()
    if intent == "DELETE_PATH":
        return not Path(data["path"]).exists()
    return True  # nessuna verifica indipendente disponibile per questo intent


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
    """Esegue un piano passo dopo passo: retry sugli errori transitori, rollback sul fallimento.

    Un passo che richiede conferma (operazione rischiosa) mette in pausa il piano senza
    eseguirlo: la sicurezza delle conferme non viene mai aggirata da una richiesta multi-step."""

    RETRYABLE_ERRORS = {"OPERATION_FAILED", "NETWORK_UNAVAILABLE"}
    MAX_ATTEMPTS = 2

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
            if step_outcome.result.success and not _verify_step(step_outcome):
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
        attempts = 0
        result = None
        while attempts < self.MAX_ATTEMPTS:
            attempts += 1
            result = self.skill_registry.execute(step.intent, step.parameters)
            if result is None:
                result = SkillResult(success=False, data={}, error="UNKNOWN_INTENT")
                break
            if result.success or result.error not in self.RETRYABLE_ERRORS:
                break
        return StepOutcome(step=step, result=result, attempts=attempts)

    def _rollback(self, completed_steps: list) -> list:
        rolled_back = []
        for step_outcome in reversed(completed_steps):
            handler = ROLLBACK_HANDLERS.get(step_outcome.step.intent)
            if handler is None:
                continue
            try:
                handler(self.skill_registry, step_outcome.step, step_outcome.result)
                step_outcome.rolled_back = True
                rolled_back.append(step_outcome)
            except Exception:
                pass
        return rolled_back
