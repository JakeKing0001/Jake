"""Test unitari per il resto di JakeCore rimasto senza copertura dopo test_jake_core_pipeline.py
e test_jake_core_permissions.py: describe_command, apply_correction, resolve_command,
format_due_reminder, i callback di notifica di default, _agent_context. intent_patterns.
match_meta_command ha gia' una propria suite (tests/test_intent_patterns.py): qui non si
retesta quella logica, solo come JakeCore la usa/delega."""
import unittest
from unittest import mock

from core.agent import AgentOutcome
from core.command import Command
from core.conversation_state import ConversationStateManager
from core.jake_core import JakeCore
from core.policy_engine import PolicyEngine
from core.skill_result import SkillResult


class FakeSkill:
    def __init__(self, metadata):
        self.metadata = metadata

    def execute(self, parameters=None):
        return SkillResult(success=True, data={})


class FakeRegistry:
    def __init__(self, skills=None):
        self._skills = skills or {}

    def get_skill(self, intent):
        return self._skills.get(intent)

    def execute(self, intent, parameters=None, policy_engine=None):
        skill = self.get_skill(intent)
        return None if skill is None else skill.execute(parameters)


class FakeRouter:
    def __init__(self, command=None):
        self.command = command or Command("UNKNOWN", {})
        self.last_route = "llm"

    def detect_intent(self, text):
        return self.command


class FakeOrchestrator:
    def __init__(self, outcome=None):
        self.outcome = outcome

    def run(self, request, history=None, trace_id=None, private=False):
        return self.outcome


class FakeLearning:
    def __init__(self):
        self.corrections = []

    def correct(self, previous_text, previous_command, command):
        self.corrections.append((previous_text, previous_command, command))

    def observe(self, *a, **k):
        pass

    def commit_pending(self):
        pass


class FakeNormalizer:
    def normalize(self, text):
        return (text or "").strip()


def _bare_core(**overrides) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.logger = mock.MagicMock()
    core.skill_registry = overrides.get("skill_registry", FakeRegistry())
    core.normalizer = overrides.get("normalizer", FakeNormalizer())
    core.conversation_state = overrides.get("conversation_state", ConversationStateManager())
    core.router = overrides.get("router", FakeRouter())
    core.orchestrator = overrides.get("orchestrator", FakeOrchestrator())
    core.learning = overrides.get("learning", FakeLearning())
    core.last_exchange = overrides.get("last_exchange")
    core.last_route = None
    core.event_bus = mock.MagicMock()
    core.desktop_context = overrides.get("desktop_context", mock.MagicMock())
    core.notification_center = mock.MagicMock()
    # PolicyEngine vera (non un MagicMock): _execute_command chiama decide_interactive_with_reason()
    # (F1.2.6), che restituisce una vera tupla (PolicyDecision, motivazione) - un MagicMock senza
    # quel metodo configurato non e' iterabile in due valori e romperebbe l'unpacking.
    core.policy_engine = overrides.get("policy_engine", PolicyEngine())
    core.action_ledger = mock.MagicMock()
    core.session_recorder = mock.MagicMock()
    core.private_mode = False
    core.model = "test-model"
    core.skill_forge = mock.MagicMock(is_available=lambda: False)
    core.planner_provider = mock.MagicMock(build_plan=lambda text: None)
    core.plan_executor = mock.MagicMock()
    return core


class DescribeCommandTests(unittest.TestCase):
    def test_run_workflow_uses_the_automation_name(self):
        core = _bare_core()
        result = core.describe_command(Command("RUN_WORKFLOW", {"name": "buonanotte"}))
        self.assertEqual(result, "eseguo l'automazione «buonanotte»")

    def test_uses_the_skills_own_description_lowercased(self):
        skill = FakeSkill({"description": "Apre un'applicazione. Usalo per aprire programmi."})
        core = _bare_core(skill_registry=FakeRegistry({"OPEN_APP": skill}))
        result = core.describe_command(Command("OPEN_APP", {"app": "chrome"}))
        self.assertEqual(result, "apre un'applicazione (app: chrome)")

    def test_falls_back_to_the_intent_name_without_a_registered_skill(self):
        # describe_command abbassa sempre la prima lettera (e' pensato per infilarsi in una
        # frase, "eseguo X"): con l'intent stesso come fallback il risultato e' quindi
        # "gHOST_INTENT", non "GHOST_INTENT" - comportamento reale, non un errore di battitura.
        core = _bare_core(skill_registry=FakeRegistry({}))
        result = core.describe_command(Command("GHOST_INTENT", {}))
        self.assertIn("HOST_INTENT", result)

    def test_falsy_parameter_values_are_omitted(self):
        skill = FakeSkill({"description": "Cancella un file."})
        core = _bare_core(skill_registry=FakeRegistry({"DELETE_PATH": skill}))
        result = core.describe_command(Command("DELETE_PATH", {"path": "C:\\a.txt", "force": False, "note": ""}))
        self.assertEqual(result, "cancella un file (path: C:\\a.txt)")


class ResolveCommandTests(unittest.TestCase):
    def test_normalizes_before_detecting_the_intent(self):
        router = FakeRouter(Command("GET_TIME", {}))
        normalizer = mock.MagicMock()
        normalizer.normalize.return_value = "normalizzato"
        core = _bare_core(router=router, normalizer=normalizer)
        command = core.resolve_command("Che Ore Sono?")
        self.assertEqual(command.intent, "GET_TIME")
        normalizer.normalize.assert_called_once_with("Che Ore Sono?")


class ApplyCorrectionTests(unittest.TestCase):
    def test_an_unknown_correction_falls_back_to_no_plan_message(self):
        core = _bare_core(router=FakeRouter(Command("UNKNOWN", {})), orchestrator=FakeOrchestrator(None))
        response = core.apply_correction("qualcosa di incomprensibile")
        self.assertIn("Non ho capito nemmeno la correzione", response)

    def test_an_unknown_correction_that_the_agent_can_handle_is_returned(self):
        outcome = AgentOutcome(final_answer="Fatto.")
        core = _bare_core(router=FakeRouter(Command("UNKNOWN", {})), orchestrator=FakeOrchestrator(outcome))
        response = core.apply_correction("fai qualcosa di strano")
        self.assertEqual(response, "Fatto.")

    def test_a_recognized_correction_related_to_the_previous_exchange_is_learned(self):
        learning = FakeLearning()
        registry = FakeRegistry({"OPEN_APP": FakeSkill({"description": "Apre un'app."})})
        core = _bare_core(
            router=FakeRouter(Command("OPEN_APP", {"app": "spotify"})), learning=learning, skill_registry=registry,
            last_exchange={"text": "apri spotify per favore", "command": Command("UNKNOWN", {}), "response": "Non so ancora fare questa cosa"},
        )
        core.apply_correction("apri spotify")
        self.assertEqual(len(learning.corrections), 1)

    def test_a_recognized_correction_unrelated_to_the_previous_exchange_is_not_learned(self):
        learning = FakeLearning()
        registry = FakeRegistry({"OPEN_APP": FakeSkill({"description": "Apre un'app."})})
        core = _bare_core(
            router=FakeRouter(Command("OPEN_APP", {"app": "spotify"})), learning=learning, skill_registry=registry,
            last_exchange={"text": "che ore sono", "command": Command("GET_TIME", {}), "response": "Sono le dieci"},
        )
        core.apply_correction("apri spotify")
        self.assertEqual(learning.corrections, [])

    def test_no_previous_exchange_does_not_crash_and_still_executes(self):
        registry = FakeRegistry({"OPEN_APP": FakeSkill({"description": "Apre un'app."})})
        core = _bare_core(router=FakeRouter(Command("OPEN_APP", {"app": "spotify"})), skill_registry=registry, last_exchange=None)
        response = core.apply_correction("apri spotify")
        self.assertIsInstance(response, str)


class FormatDueReminderTests(unittest.TestCase):
    def test_a_generic_timer_uses_a_generic_message(self):
        result = JakeCore.format_due_reminder({"kind": "timer", "text": "timer"})
        self.assertEqual(result, "Il timer è scaduto!")

    def test_a_named_timer_includes_its_label(self):
        result = JakeCore.format_due_reminder({"kind": "timer", "text": "pasta"})
        self.assertEqual(result, "Il timer per pasta è scaduto!")

    def test_a_regular_reminder_uses_its_text(self):
        result = JakeCore.format_due_reminder({"text": "chiama il dentista"})
        self.assertEqual(result, "Promemoria: chiama il dentista")


class DefaultNotificationCallbacksTests(unittest.TestCase):
    def test_reminder_due_notifies_with_the_formatted_message(self):
        core = _bare_core()
        with mock.patch.object(core, "notify", return_value="Promemoria: chiama il dentista") as notify:
            with mock.patch("builtins.print"):
                core._default_on_reminder_due({"text": "chiama il dentista"})
        notify.assert_called_once_with("reminder", "Promemoria: chiama il dentista")

    def test_reminder_due_prints_nothing_when_notify_queues_it(self):
        core = _bare_core()
        with mock.patch.object(core, "notify", return_value=None):
            with mock.patch("builtins.print") as printed:
                core._default_on_reminder_due({"text": "chiama il dentista"})
        printed.assert_not_called()

    def test_advisory_notifies_with_the_given_message(self):
        core = _bare_core()
        with mock.patch.object(core, "notify", return_value="batteria scarica") as notify:
            with mock.patch("builtins.print"):
                core._default_on_advisory("batteria scarica")
        notify.assert_called_once_with("advisory", "batteria scarica")

    def test_advisory_prints_nothing_when_notify_queues_it(self):
        core = _bare_core()
        with mock.patch.object(core, "notify", return_value=None):
            with mock.patch("builtins.print") as printed:
                core._default_on_advisory("batteria scarica")
        printed.assert_not_called()

    def test_trigger_fired_notifies_with_a_summary(self):
        core = _bare_core()
        outcome = mock.MagicMock(completed=[], success=True, stopped_step=None)
        with mock.patch("core.jake_core.format_plan_outcome", return_value="riepilogo"):
            with mock.patch.object(core, "notify", return_value="notificato") as notify:
                with mock.patch("builtins.print"):
                    core._default_on_trigger_fired({"name": "buonanotte"}, outcome, 2)
        args = notify.call_args.args
        self.assertEqual(args[0], "trigger")
        self.assertIn("buonanotte", args[1])
        self.assertIn("riepilogo", args[1])

    def test_trigger_fired_forwards_the_outcomes_trace_id(self):
        """F1.7.2 ("collegare... notifica con lo stesso trace id"): senza questo, una notifica
        'Ho eseguito X' non aveva nessun modo di essere ricollegata alle ricevute nel ledger che
        quella stessa esecuzione ha gia' prodotto - vedi PlanOutcome.trace_id."""
        core = _bare_core()
        outcome = mock.MagicMock(completed=[], success=True, stopped_step=None, trace_id="abc123")
        with mock.patch("core.jake_core.format_plan_outcome", return_value="riepilogo"):
            with mock.patch.object(core, "notify", return_value="notificato") as notify:
                with mock.patch("builtins.print"):
                    core._default_on_trigger_fired({"name": "buonanotte"}, outcome, 2)
        self.assertEqual(notify.call_args.kwargs.get("trace_id"), "abc123")


class AgentContextTests(unittest.TestCase):
    def test_combines_desktop_and_entity_context(self):
        desktop_context = mock.MagicMock(context_summary=lambda: "Visual Studio Code")
        core = _bare_core(desktop_context=desktop_context)
        core.conversation_state.remember_entities("OPEN_APP", {"app": "spotify"}, {})
        result = core._agent_context()
        self.assertIn("Visual Studio Code", result)
        self.assertIn("spotify", result)

    def test_empty_context_pieces_are_omitted(self):
        desktop_context = mock.MagicMock(context_summary=lambda: "")
        core = _bare_core(desktop_context=desktop_context)
        result = core._agent_context()
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
