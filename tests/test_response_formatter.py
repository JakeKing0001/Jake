"""Test unitari per core/response_formatter.py::format_plan_outcome. Nessuna suite dedicata
esisteva finora - il modulo era esercitato solo indirettamente, con format_plan_outcome() sempre
mockato nei test di jake_core.py (verificano il COLLEGAMENTO, mai il CONTENUTO del messaggio
prodotto). Qui si costruiscono PlanOutcome/StepOutcome direttamente (dataclass semplici, non serve
un vero PlanExecutor) per provare cosa l'utente legge davvero in ogni scenario."""
import unittest

from core.plan_executor import PlanOutcome, StepOutcome
from core.planner import PlanStep
from core.response_formatter import format_plan_outcome
from core.skill_result import SkillResult


def _step(intent: str, description: str = "", success: bool = True, error: str | None = None, rolled_back: bool = False) -> StepOutcome:
    step = PlanStep(intent=intent, parameters={}, description=description)
    result = SkillResult(success=success, data={}, error=error)
    return StepOutcome(step=step, result=result, attempts=1, rolled_back=rolled_back)


class SuccessfulPlanTests(unittest.TestCase):
    def test_every_step_listed_as_done_with_no_failure_language(self):
        outcome = PlanOutcome(completed=[_step("ADD_NOTE", "aggiungi un appunto"), _step("GET_TIME", "che ore sono")])

        message = format_plan_outcome(outcome, total_steps=2)

        self.assertIn("Ho completato i 2 passi richiesti", message)
        self.assertIn("fatto: aggiungi un appunto", message)
        self.assertIn("fatto: che ore sono", message)
        self.assertNotIn("fallito", message)
        self.assertNotIn("annullato", message)


class PausedForConfirmationTests(unittest.TestCase):
    def test_a_step_requiring_confirmation_is_reported_as_paused_not_failed(self):
        stopped = _step("DELETE_PATH", "cancella il file", success=False, error="CONFIRMATION_REQUIRED")
        stopped.result = SkillResult(success=False, data={"message": "confermi la cancellazione?"}, error="CONFIRMATION_REQUIRED")
        outcome = PlanOutcome(completed=[_step("ADD_NOTE", "aggiungi un appunto")], stopped_step=stopped)

        message = format_plan_outcome(outcome, total_steps=2)

        self.assertIn("in pausa: cancella il file", message)
        self.assertIn("confermi la cancellazione?", message)
        self.assertNotIn("fallito", message)
        self.assertNotIn("annullato", message, "una pausa non e' un fallimento: non deve mai parlare di rollback")


class FailedPlanWithoutRollbackTests(unittest.TestCase):
    def test_a_failure_with_nothing_completed_before_it_mentions_no_rollback(self):
        stopped = _step("DELETE_PATH", "cancella il file", success=False, error="OPERATION_FAILED")
        outcome = PlanOutcome(completed=[], stopped_step=stopped)

        message = format_plan_outcome(outcome, total_steps=1)

        self.assertIn("fallito: cancella il file", message)
        self.assertNotIn("annullato", message)
        self.assertNotIn("restano invece attivi", message)


class PartialRollbackHonestyTests(unittest.TestCase):
    """F1.3.7 ("gestire effetti parziali e rollback parziale con spiegazione leggibile"): buco
    reale riprodotto prima di correggerlo - un passo completato ma SENZA un inverso noto (es.
    KILL_PROCESS_BY_PORT) o il cui rollback fallisce non compare mai in outcome.rolled_back; il
    messaggio elencava solo cio' che era stato annullato, senza mai dire che un ALTRO effetto gia'
    avvenuto per davvero restava silenziosamente attivo."""

    def test_a_step_without_a_known_inverse_is_reported_as_still_active(self):
        undone_step = _step("CREATE_PATH", "crea il file", rolled_back=True)
        persisting_step = _step("KILL_PROCESS_BY_PORT", "termina il processo sulla porta 8080", rolled_back=False)
        stopped = _step("FAILING_STEP", "passo che fallisce", success=False, error="OPERATION_FAILED")
        outcome = PlanOutcome(completed=[undone_step, persisting_step], rolled_back=[undone_step], stopped_step=stopped)

        message = format_plan_outcome(outcome, total_steps=3)

        self.assertIn("Ho annullato i passi precedenti per sicurezza: crea il file.", message)
        self.assertIn("restano invece attivi", message)
        self.assertIn("termina il processo sulla porta 8080", message)

    def test_when_every_completed_step_is_rolled_back_nothing_is_reported_as_persisting(self):
        undone_step = _step("CREATE_PATH", "crea il file", rolled_back=True)
        stopped = _step("FAILING_STEP", "passo che fallisce", success=False, error="OPERATION_FAILED")
        outcome = PlanOutcome(completed=[undone_step], rolled_back=[undone_step], stopped_step=stopped)

        message = format_plan_outcome(outcome, total_steps=2)

        self.assertIn("Ho annullato i passi precedenti per sicurezza: crea il file.", message)
        self.assertNotIn("restano invece attivi", message, "tutto e' stato annullato: non c'e' nulla che resta attivo da segnalare")

    def test_when_nothing_can_be_rolled_back_every_completed_step_is_reported_as_persisting(self):
        """Il caso estremo: NESSUno dei passi completati ha un inverso noto - outcome.rolled_back
        resta vuoto (nessuna riga "ho annullato"), ma l'utente deve comunque sapere che quegli
        effetti restano attivi, non solo che il piano si e' fermato."""
        persisting_step = _step("KILL_PROCESS_BY_PORT", "termina il processo sulla porta 8080", rolled_back=False)
        stopped = _step("FAILING_STEP", "passo che fallisce", success=False, error="OPERATION_FAILED")
        outcome = PlanOutcome(completed=[persisting_step], rolled_back=[], stopped_step=stopped)

        message = format_plan_outcome(outcome, total_steps=2)

        self.assertNotIn("Ho annullato", message)
        self.assertIn("restano invece attivi", message)
        self.assertIn("termina il processo sulla porta 8080", message)


if __name__ == "__main__":
    unittest.main()
