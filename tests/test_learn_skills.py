"""Test unitari per skills/learn.py: nessuna suite esisteva finora. Il "core" e' un MagicMock
con spec esplicita (solo gli attributi che le skill leggono davvero): niente Ollama/NEST veri,
coerente con l'approccio "oggetto spoglio" gia' usato altrove in questa sessione per JakeCore.

F1: buco reale, grave, trovato e corretto in questa sessione. LearnCommandSkill.execute()
leggeva self.core.MULTI_STEP_PATTERN, un attributo che JakeCore non ha MAI avuto (il pattern
vive in core/intent_patterns.py, usato altrove da JakeCore solo tramite la funzione
is_multi_step_request(), mai riesposto come attributo). Verificato per davvero: la skill
sollevava un AttributeError a OGNI singola chiamata - l'intera funzionalita' "impara che quando
dico X fai Y" era completamente rotta, con l'utente che vedeva solo un generico "si e' verificato
un errore imprevisto" (JakeCore.answer() cattura tutto), mai il vero motivo."""
import unittest
from unittest import mock

from core.command import Command
from core.planner import Plan, PlanStep
from skills.learn import CorrectLastSkill, ForgetLearnedSkill, LearnCommandSkill, ListLearnedSkill


def _fake_core(**overrides):
    core = mock.MagicMock(spec=[
        "normalizer", "router", "planner_provider", "skill_registry", "learning",
        "describe_command", "apply_correction",
    ])
    core.normalizer.normalize.side_effect = lambda text: (text or "").strip()
    core.describe_command.return_value = "descrizione"
    for key, value in overrides.items():
        setattr(core, key, value)
    return core


class LearnCommandCrashRegressionTests(unittest.TestCase):
    """Il buco reale trovato e corretto in questa sessione: non deve MAI sollevare
    AttributeError, per nessuna combinazione ragionevole di input."""

    def test_a_simple_single_step_request_does_not_crash(self):
        core = _fake_core()
        core.router.detect_intent.return_value = Command("OPEN_APP", {"app": "spotify"})
        result = LearnCommandSkill(core).execute({"phrase": "buongiorno", "request": "apri spotify"})
        self.assertTrue(result.success)

    def test_a_multi_step_request_does_not_crash(self):
        core = _fake_core()
        core.planner_provider.build_plan.return_value = Plan(steps=[
            PlanStep(intent="OPEN_APP", parameters={"app": "spotify"}),
            PlanStep(intent="SET_VOLUME", parameters={"action": "up"}),
        ])
        result = LearnCommandSkill(core).execute({
            "phrase": "buongiorno", "request": "apri spotify e poi alza il volume",
        })
        self.assertTrue(result.success)


class LearnCommandSingleStepTests(unittest.TestCase):
    def test_learns_a_single_command_and_teaches_it(self):
        core = _fake_core()
        core.router.detect_intent.return_value = Command("OPEN_APP", {"app": "spotify"})
        result = LearnCommandSkill(core).execute({"phrase": "musica", "request": "apri spotify"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["kind"], "command")
        self.assertEqual(result.data["intent"], "OPEN_APP")
        core.learning.teach.assert_called_once_with("musica", "OPEN_APP", {"app": "spotify"})

    def test_missing_phrase_or_request_fails(self):
        core = _fake_core()
        self.assertEqual(LearnCommandSkill(core).execute({"request": "apri spotify"}).error, "MISSING_PARAMETERS")
        self.assertEqual(LearnCommandSkill(core).execute({"phrase": "musica"}).error, "MISSING_PARAMETERS")


class LearnCommandUnknownIntentFallsBackToWorkflowTests(unittest.TestCase):
    def test_unrecognized_single_intent_falls_back_to_a_workflow_plan(self):
        core = _fake_core()
        core.router.detect_intent.return_value = Command("UNKNOWN", {})
        core.planner_provider.build_plan.return_value = Plan(steps=[
            PlanStep(intent="OPEN_APP", parameters={"app": "spotify"}),
        ])

        result = LearnCommandSkill(core).execute({"phrase": "musica", "request": "fai qualcosa di complesso"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["kind"], "workflow")
        self.assertEqual(result.data["intent"], "RUN_WORKFLOW")
        core.skill_registry.workflow_manager.save.assert_called_once_with("musica", core.planner_provider.build_plan.return_value)
        core.learning.teach.assert_called_once_with("musica", "RUN_WORKFLOW", {"name": "musica"})

    def test_unrecognized_intent_with_no_plan_at_all_fails(self):
        core = _fake_core()
        core.router.detect_intent.return_value = Command("UNKNOWN", {})
        core.planner_provider.build_plan.return_value = None

        result = LearnCommandSkill(core).execute({"phrase": "musica", "request": "boh"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "PLAN_FAILED")
        core.learning.teach.assert_not_called()


class LearnCommandMultiStepTests(unittest.TestCase):
    def test_a_multi_step_request_is_saved_as_a_workflow(self):
        core = _fake_core()
        plan = Plan(steps=[
            PlanStep(intent="OPEN_APP", parameters={"app": "spotify"}),
            PlanStep(intent="SET_VOLUME", parameters={"action": "up"}),
        ])
        core.planner_provider.build_plan.return_value = plan

        result = LearnCommandSkill(core).execute({
            "phrase": "routine mattina", "request": "apri spotify e poi alza il volume",
        })

        self.assertTrue(result.success)
        self.assertEqual(result.data["kind"], "workflow")
        core.skill_registry.workflow_manager.save.assert_called_once_with("routine mattina", plan)
        core.router.detect_intent.assert_not_called()

    def test_a_multi_step_request_with_too_few_planned_steps_falls_back_to_single_intent(self):
        """Se il planner produce solo UN passo, non vale la pena salvarlo come workflow: si
        ripiega sul percorso a intent singolo, esattamente come per una richiesta 'e poi' che
        il planner riduce a una singola azione (es. il combo browser)."""
        core = _fake_core()
        core.planner_provider.build_plan.return_value = Plan(steps=[PlanStep(intent="OPEN_APP", parameters={})])
        core.router.detect_intent.return_value = Command("OPEN_APP", {"app": "spotify"})

        result = LearnCommandSkill(core).execute({"phrase": "musica", "request": "apri spotify e poi"})

        self.assertEqual(result.data["kind"], "command")
        core.skill_registry.workflow_manager.save.assert_not_called()


class ListLearnedSkillTests(unittest.TestCase):
    def test_no_taught_commands_reports_not_found(self):
        core = _fake_core()
        core.learning.list_taught.return_value = []
        result = ListLearnedSkill(core).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_taught_commands_with_descriptions(self):
        core = _fake_core()
        example = mock.MagicMock(text="musica", intent="OPEN_APP", parameters={"app": "spotify"})
        core.learning.list_taught.return_value = [example]
        core.learning.count_auto.return_value = 3

        result = ListLearnedSkill(core).execute({})

        self.assertTrue(result.success)
        self.assertEqual(result.data["commands"][0]["phrase"], "musica")
        self.assertEqual(result.data["auto_count"], 3)


class ForgetLearnedSkillTests(unittest.TestCase):
    def test_missing_phrase_fails(self):
        result = ForgetLearnedSkill(_fake_core()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_forgetting_an_unknown_phrase_reports_not_found(self):
        core = _fake_core()
        core.learning.forget.return_value = False
        result = ForgetLearnedSkill(core).execute({"phrase": "non esiste"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_forgetting_a_known_phrase_succeeds(self):
        core = _fake_core()
        core.learning.forget.return_value = True
        result = ForgetLearnedSkill(core).execute({"phrase": "musica"})
        self.assertTrue(result.success)
        core.learning.forget.assert_called_once_with("musica")


class CorrectLastSkillTests(unittest.TestCase):
    def test_missing_request_fails(self):
        result = CorrectLastSkill(_fake_core()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_delegates_to_apply_correction_and_wraps_the_response(self):
        core = _fake_core()
        core.apply_correction.return_value = "ho aperto spotify"
        result = CorrectLastSkill(core).execute({"request": "aprimi spotify"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["response"], "ho aperto spotify")
        core.apply_correction.assert_called_once_with("aprimi spotify")


if __name__ == "__main__":
    unittest.main()
