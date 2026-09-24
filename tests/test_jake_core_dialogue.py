"""F2.6 regressions at the real JakeCore policy/dialogue boundary, without desktop I/O."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core.action_contracts import UndoDescriptor
from core.auth_gate import AuthGate
from core.command import Command
from core.jake_core import JakeCore
from core.memory_manager import MemoryManager
from core.profiles import ProfileManager
from core.request_context import reset_current_speaker_profile_id, set_current_speaker_profile_id
from core.skill_result import SkillResult
from core.voice.dialogue_runtime import DialogueRuntime
from tests.test_jake_core_pipeline import FakeRegistry, FakeRouter, FakeSkill, _bare_core


class JakeCoreDialogueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.skill = FakeSkill()
        self.undo = FakeSkill()
        self.learning = mock.Mock()
        self.core = _bare_core(
            ledger_path=self.root / "ledger.jsonl", learning=self.learning,
            auth_gate=AuthGate(passphrase="MiXeD secret 42!"),
            skill_registry=FakeRegistry({"OPEN_APP": self.skill, "CLOSE_WINDOW": self.undo}),
            router=FakeRouter(Command("OPEN_APP", {"app": "spotify"})),
        )

    def record(self, intent="UNKNOWN", status="failed", reversible=False, action_id="source"):
        command = Command(intent, {"app": "spotifi"})
        self.core._set_dialogue_outcome(
            action_id=action_id, text="apri spotifi", command=command,
            status=status, reversible=reversible,
        )
        self.core._remember_exchange("apri spotifi", command, "risposta", action_id=action_id)
        return self.core._get_dialogue_runtime().turn(action_id)

    def prepare_undo(self):
        self.record("OPEN_APP", "executed", reversible=True)
        self.core.undo_store.save(UndoDescriptor(
            action_id="source", compensating_intent="CLOSE_WINDOW",
            compensating_parameters={"title": "spotifi"}, expires_at=time.time() + 60,
        ))
        self.assertIn("Confermi", self.core.apply_correction("apri spotify"))
        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.undo.calls, [])

    def test_minimal_core_keeps_one_lazy_runtime(self):
        core = JakeCore.__new__(JakeCore)
        runtime = core._get_dialogue_runtime()
        self.assertIsInstance(runtime, DialogueRuntime)
        self.assertIs(core._get_dialogue_runtime(), runtime)
        other = JakeCore.__new__(JakeCore)
        self.assertIsNot(other._get_dialogue_runtime(), runtime)

    def test_existing_runtime_and_its_history_are_preserved(self):
        runtime = DialogueRuntime()
        self.core.dialogue_runtime = runtime
        self.record()
        self.assertIs(self.core._get_dialogue_runtime(), runtime)
        self.assertEqual(self.core._what_did_you_hear(), 'Ho sentito: "apri spotifi".')

    def test_unknown_exchange_replaces_an_older_executed_action(self):
        self.record("SEND_EMAIL", "executed")
        self.core._remember_exchange("apri spotifi", Command("UNKNOWN", {}), self.core.NO_PLAN)
        self.core.apply_correction("apri spotify")
        self.assertEqual(len(self.skill.calls), 1)
        self.learning.correct.assert_called_once()
        self.assertEqual(self.learning.correct.call_args.args[0], "apri spotifi")

    def test_completed_external_action_never_reaches_agent_fallback(self):
        self.record("SEND_EMAIL", "executed")
        self.core.router = FakeRouter(Command("UNKNOWN", {}))
        with mock.patch.object(self.core, "_run_agent") as agent:
            self.assertIn("Non la ripeto", self.core.apply_correction("rispedisci il messaggio"))
        agent.assert_not_called()
        self.assertEqual(self.skill.calls, [])

    def test_unknown_correction_does_not_skip_required_undo(self):
        self.record("OPEN_APP", "executed", reversible=True)
        self.core.router = FakeRouter(Command("UNKNOWN", {}))
        with mock.patch.object(self.core, "_run_agent") as agent:
            self.assertIn("Non ho capito", self.core.apply_correction("qualcos'altro"))
        agent.assert_not_called()

    def test_corrected_action_still_obeys_policy_block(self):
        self.record()
        self.core.policy_engine.blocked_intents.add("OPEN_APP")
        self.core.apply_correction("apri spotify")
        self.assertEqual(self.skill.calls, [])
        self.learning.correct.assert_not_called()

    def test_correction_learning_waits_for_confirmation_and_success(self):
        self.record()
        self.core.policy_engine.always_confirm_intents.add("OPEN_APP")
        self.core.apply_correction("apri spotify")
        pending = self.core.conversation_state.get_pending_action()
        self.learning.correct.assert_not_called()
        self.assertEqual(self.skill.calls, [])
        self.core._process("sì")
        self.assertEqual(len(self.skill.calls), 1)
        self.learning.correct.assert_called_once()
        self.assertEqual(self.core.last_exchange["action_id"], pending["action_id"])
        self.assertEqual(self.core._get_dialogue_runtime().turn(pending["action_id"]).status, "executed")
        self.assertEqual(self.core.action_ledger.read_all()[-1]["action_id"], pending["action_id"])

    def test_failed_correction_is_not_learned(self):
        self.record()
        self.skill.result = SkillResult(success=False, error="TEST_FAILED")
        self.core.apply_correction("apri spotify")
        self.learning.correct.assert_not_called()

    def test_cancelled_correction_is_not_learned(self):
        self.record()
        self.core.policy_engine.always_confirm_intents.add("OPEN_APP")
        self.core.apply_correction("apri spotify")
        self.core._process("no")
        self.learning.correct.assert_not_called()
        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.core._get_dialogue_runtime().planner.pending_learning_count, 0)

    def test_extended_yes_cancels_instead_of_confirming(self):
        self.core.policy_engine.always_confirm_intents.add("OPEN_APP")
        self.core._execute_command("apri spotifi", Command("OPEN_APP", {"app": "spotifi"}))
        action_id = self.core.last_exchange["action_id"]
        self.core._process("sì ma prima apri spotify")
        self.assertEqual(self.skill.calls, [])
        self.assertEqual(self.core._get_dialogue_runtime().turn(action_id).status, "cancelled")

    def test_policy_revocation_after_confirmation_prevents_execution(self):
        self.core.policy_engine.always_confirm_intents.add("OPEN_APP")
        self.core._execute_command("apri spotify", Command("OPEN_APP", {"app": "spotify"}))
        self.core.policy_engine.blocked_intents.add("OPEN_APP")
        self.core._process("sì")
        self.assertEqual(self.skill.calls, [])

    def test_successful_undo_runs_correction_once(self):
        self.prepare_undo()
        self.core._process("sì")
        self.assertEqual(len(self.undo.calls), 1)
        self.assertEqual(len(self.skill.calls), 1)
        self.assertIsNone(self.core.undo_store.get("source"))
        self.learning.correct.assert_called_once()

    def test_failed_undo_stops_correction_and_learning(self):
        self.prepare_undo()
        self.undo.result = SkillResult(success=False, error="TEST_FAILED")
        self.core._process("sì")
        self.assertEqual(len(self.undo.calls), 1)
        self.assertEqual(self.skill.calls, [])
        self.learning.correct.assert_not_called()

    def test_consumed_undo_is_rechecked_when_confirmed(self):
        self.prepare_undo()
        self.core.undo_store.mark_used("source")
        self.assertIn("non è più disponibile", self.core._process("sì"))
        self.assertEqual(self.undo.calls, [])
        self.assertEqual(self.skill.calls, [])

    def test_authentication_during_undo_keeps_correction_continuation(self):
        self.prepare_undo()
        self.core.policy_engine.require_auth_intents.add("CLOSE_WINDOW")
        self.core._process("sì")
        self.assertEqual(self.core.conversation_state.get_pending_action()["reason"], "auth_required")
        self.assertEqual(self.core._get_dialogue_runtime().last_turn("local").utterance_id, "source")
        self.core.answer("MiXeD secret 42!")
        self.assertEqual(len(self.undo.calls), 1)
        self.assertEqual(len(self.skill.calls), 1)
        self.learning.correct.assert_called_once()

    def test_auth_secret_is_raw_and_never_recorded_in_profile(self):
        core = self.core
        core.memory_manager = MemoryManager(self.root / "default.db")
        self.addCleanup(core.memory_manager.close)
        core.profile_manager = ProfileManager(self.root / "profiles")
        namespace = core.profile_manager.create_profile("davide", "Davide")
        core._profile_turn_lock = threading.RLock()
        core._active_profile_namespace = None
        core.policy_engine.require_auth_intents.add("OPEN_APP")
        token = set_current_speaker_profile_id("davide")
        try:
            core._run_in_current_profile(lambda: core._execute_command(
                "apri spotify", Command("OPEN_APP", {"app": "spotify"}),
            ))
            self.assertFalse(core.conversation_state.has_pending_action())
            core.normalizer = mock.Mock()
            core.event_bus = mock.Mock()
            core.logger = mock.Mock()
            with mock.patch.object(core.memory_manager, "log_turn") as log_turn:
                core.answer("MiXeD secret 42!")
            core.normalizer.normalize.assert_not_called()
            log_turn.assert_not_called()
        finally:
            reset_current_speaker_profile_id(token)
        self.assertEqual(len(self.skill.calls), 1)
        self.assertEqual(namespace.conversation.get_short_term_history(), [])
        self.assertIsNone(namespace.conversation.get_pending_action())
        for calls in (core.event_bus.mock_calls, core.logger.mock_calls, self.learning.mock_calls):
            self.assertNotIn("MiXeD secret 42!", repr(calls))
        self.assertEqual(core._in_flight_answers, 0)


if __name__ == "__main__":
    unittest.main()
