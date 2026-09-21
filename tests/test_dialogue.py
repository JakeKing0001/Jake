"""Test per core/voice/dialogue.py (F2.6.3, F2.6.4, F2.6.6, F2.6.7). Puro testo e decisioni."""
import unittest

from core.risk import RiskLevel
from core.voice.dialogue import (
    CorrectionPlanner, DialogueContext, ReplyKind, classify_reply, needs_confirmation,
)


class ClassifyReplyTests(unittest.TestCase):
    def test_without_anything_pending_everything_is_a_new_command(self):
        for text in ("apri spotify", "si", "no", "che ore sono"):
            with self.subTest(text=text):
                self.assertEqual(classify_reply(text, DialogueContext()).kind, ReplyKind.NEW_COMMAND)

    def test_yes_and_no_answer_a_pending_confirmation(self):
        ctx = DialogueContext(pending_confirmation=True)
        for text in ("si", "Sì.", "sì certo", "ok", "confermo", "procedi", "vai pure", "Jake, si", "yes", "go ahead"):
            with self.subTest(text=text):
                self.assertEqual(classify_reply(text, ctx).kind, ReplyKind.CONFIRM_YES)
        for text in ("no", "annulla", "lascia stare", "non farlo", "fermati", "cancel"):
            with self.subTest(text=text):
                self.assertEqual(classify_reply(text, ctx).kind, ReplyKind.CONFIRM_NO)

    def test_a_yes_with_something_else_is_a_new_command_and_cancels_the_pending_action(self):
        reply = classify_reply("si ma prima apri spotify", DialogueContext(pending_confirmation=True))
        self.assertEqual(reply.kind, ReplyKind.NEW_COMMAND)
        self.assertTrue(reply.cancels_pending)  # mai lasciare armata una cancellazione mentre si parla d'altro

    def test_an_unrelated_request_during_a_confirmation_cancels_it(self):
        reply = classify_reply("che ore sono", DialogueContext(pending_confirmation=True))
        self.assertEqual((reply.kind, reply.cancels_pending), (ReplyKind.NEW_COMMAND, True))

    def test_a_plain_command_without_a_pending_confirmation_does_not_cancel_anything(self):
        self.assertFalse(classify_reply("apri spotify", DialogueContext()).cancels_pending)

    def test_while_waiting_for_a_password_everything_is_the_secret(self):
        ctx = DialogueContext(awaiting_auth=True, pending_confirmation=True)
        for text in ("si", "apri spotify", "jake stop", "la mia password 123"):
            with self.subTest(text=text):
                reply = classify_reply(text, ctx)
                self.assertEqual(reply.kind, ReplyKind.AUTH_SECRET)
                self.assertEqual(reply.secret, text)
                self.assertEqual(reply.text, "")  # il testo normale resta vuoto

    def test_the_secret_never_appears_in_repr_or_str(self):
        reply = classify_reply("correct-horse-battery", DialogueContext(awaiting_auth=True))
        self.assertNotIn("correct-horse-battery", repr(reply))
        self.assertNotIn("correct-horse-battery", str(reply))

    def test_short_answers_fill_a_clarification(self):
        ctx = DialogueContext(awaiting_clarification=True)
        for text in ("Marco", "quello di ieri", "il primo", "a Milano"):
            with self.subTest(text=text):
                reply = classify_reply(text, ctx)
                self.assertEqual((reply.kind, reply.text), (ReplyKind.CLARIFICATION_ANSWER, text))

    def test_a_full_command_during_a_clarification_is_a_new_command(self):
        ctx = DialogueContext(awaiting_clarification=True)
        for text in ("apri spotify", "Jake che ore sono", "chi ha vinto ieri?", "cerca su google i gatti"):
            with self.subTest(text=text):
                self.assertEqual(classify_reply(text, ctx).kind, ReplyKind.NEW_COMMAND)

    def test_a_long_rambling_reply_is_not_forced_into_a_clarification(self):
        ctx = DialogueContext(awaiting_clarification=True)
        text = "allora vediamo un po' quello che intendevo ieri sera quando parlavamo del progetto"
        self.assertEqual(classify_reply(text, ctx).kind, ReplyKind.NEW_COMMAND)

    def test_the_wake_word_is_stripped_from_the_text(self):
        reply = classify_reply("Jake apri spotify", DialogueContext())
        self.assertEqual(reply.text, "apri spotify")

    def test_empty_text(self):
        self.assertEqual(classify_reply("", DialogueContext()).kind, ReplyKind.NEW_COMMAND)


class NeedsConfirmationTests(unittest.TestCase):
    def test_reads_never_ask_even_with_a_terrible_transcript(self):
        self.assertFalse(needs_confirmation(0.05, RiskLevel.READ_ONLY).required)

    def test_local_reversible_asks_only_when_recognition_is_doubtful(self):
        self.assertFalse(needs_confirmation(0.9, RiskLevel.LOCAL_REVERSIBLE).required)
        self.assertFalse(needs_confirmation(0.5, RiskLevel.LOCAL_REVERSIBLE).required)
        self.assertTrue(needs_confirmation(0.49, RiskLevel.LOCAL_REVERSIBLE).required)

    def test_external_actions_need_a_higher_certainty(self):
        self.assertFalse(needs_confirmation(0.95, RiskLevel.EXTERNAL_ACTION).required)
        self.assertTrue(needs_confirmation(0.79, RiskLevel.EXTERNAL_ACTION).required)

    def test_destructive_and_admin_always_ask_whatever_the_confidence(self):
        for risk in (RiskLevel.DESTRUCTIVE, RiskLevel.ADMIN):
            for confidence in (0.0, 0.99, 1.0, None):
                with self.subTest(risk=risk, confidence=confidence):
                    self.assertTrue(needs_confirmation(confidence, risk).required)

    def test_unknown_confidence_is_treated_as_medium(self):
        self.assertFalse(needs_confirmation(None, RiskLevel.LOCAL_REVERSIBLE).required)
        self.assertTrue(needs_confirmation(None, RiskLevel.EXTERNAL_ACTION).required)  # 0.7 < 0.8

    def test_the_reason_explains_the_decision(self):
        self.assertIn("incerto", needs_confirmation(0.3, RiskLevel.LOCAL_REVERSIBLE).reason)
        self.assertIn("destructive", needs_confirmation(1.0, RiskLevel.DESTRUCTIVE).reason)

    def test_every_risk_level_has_a_rule(self):
        for risk in RiskLevel:
            needs_confirmation(0.6, risk)  # nessun KeyError


class CorrectionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.p = CorrectionPlanner(clock=lambda: 1000.0)

    def test_an_action_that_never_ran_can_simply_be_rerun(self):
        for status in ("pending", "failed", "cancelled"):
            self.p.record(status, "apri spotifi", "OPEN_APP", {"app": "spotifi"}, RiskLevel.LOCAL_REVERSIBLE, status)
            with self.subTest(status=status):
                self.assertEqual(self.p.plan(status).action, "rerun")

    def test_a_completed_reversible_local_action_is_undone_before_rerunning(self):
        self.p.record("u1", "apri spotifi", "OPEN_APP", {}, RiskLevel.LOCAL_REVERSIBLE, "executed", reversible=True)
        self.assertEqual(self.p.plan("u1").action, "undo_then_rerun")

    def test_a_completed_action_is_never_silently_repeated_when_it_cannot_be_undone(self):
        self.p.record("u1", "manda ciao a marco", "SEND_MESSAGE", {}, RiskLevel.EXTERNAL_ACTION, "executed", reversible=False)
        self.assertEqual(self.p.plan("u1").action, "ask")
        self.p.record("u2", "cancella il file", "DELETE_PATH", {}, RiskLevel.DESTRUCTIVE, "executed", reversible=True)
        self.assertEqual(self.p.plan("u2").action, "ask")  # reversibile ma DESTRUCTIVE: si chiede comunque

    def test_a_completed_local_action_that_is_not_reversible_asks(self):
        self.p.record("u1", "x", "OPEN_APP", {}, RiskLevel.LOCAL_REVERSIBLE, "executed", reversible=False)
        self.assertEqual(self.p.plan("u1").action, "ask")

    def test_unknown_turn(self):
        self.assertEqual(self.p.plan("mai-visto").action, "unknown")

    def test_update_status_changes_the_plan(self):
        self.p.record("u1", "x", "OPEN_APP", {}, RiskLevel.LOCAL_REVERSIBLE, "pending")
        self.assertEqual(self.p.plan("u1").action, "rerun")
        self.p.update_status("u1", "executed")
        self.assertEqual(self.p.plan("u1").action, "ask")

    def test_last_heard_returns_the_most_recent_first(self):
        for i in range(3):
            self.p.record(f"u{i}", f"frase {i}", "X", {}, RiskLevel.READ_ONLY, "executed")
        self.assertEqual([t.heard for t in self.p.last_heard(2)], ["frase 2", "frase 1"])

    def test_history_is_bounded(self):
        small = CorrectionPlanner(keep=3)
        for i in range(10):
            small.record(f"u{i}", "x", "X", {}, RiskLevel.READ_ONLY, "executed")
        self.assertEqual(small.plan("u0").action, "unknown")
        self.assertNotEqual(small.plan("u9").action, "unknown")

    def test_record_copies_the_parameters(self):
        params = {"app": "spotify"}
        self.p.record("u1", "x", "OPEN_APP", params, RiskLevel.LOCAL_REVERSIBLE, "pending")
        params["app"] = "cambiato"
        self.assertEqual(self.p.last_heard()[0].parameters, {"app": "spotify"})


class LearningGateTests(unittest.TestCase):
    def setUp(self):
        self.p = CorrectionPlanner()
        self.p.record("u1", "apri spotifi", "OPEN_APP", {"app": "spotifi"}, RiskLevel.LOCAL_REVERSIBLE, "executed")

    def test_a_verified_outcome_becomes_an_example(self):
        self.assertTrue(self.p.propose_learning("u1", "apri spotify", "OPEN_APP", {"app": "spotify"}))
        learnable = self.p.resolve_learning("u1", verified=True)
        assert learnable is not None
        self.assertEqual((learnable.heard, learnable.corrected_text, learnable.intent), ("apri spotifi", "apri spotify", "OPEN_APP"))

    def test_an_unverified_or_failed_outcome_teaches_nothing(self):
        self.p.propose_learning("u1", "apri spotify", "OPEN_APP", {"app": "spotify"})
        self.assertIsNone(self.p.resolve_learning("u1", verified=False))

    def test_the_proposal_is_consumed_either_way(self):
        self.p.propose_learning("u1", "apri spotify", "OPEN_APP", {})
        self.p.resolve_learning("u1", verified=False)
        self.assertIsNone(self.p.resolve_learning("u1", verified=True))  # non risorge con un esito successivo
        self.assertEqual(self.p.pending_learning_count, 0)

    def test_nothing_is_learned_without_a_proposal(self):
        self.assertIsNone(self.p.resolve_learning("u1", verified=True))

    def test_proposing_for_an_unknown_turn_or_empty_text_is_refused(self):
        self.assertFalse(self.p.propose_learning("boh", "x", "OPEN_APP", {}))
        self.assertFalse(self.p.propose_learning("u1", "   ", "OPEN_APP", {}))

    def test_a_proposal_alone_does_not_count_as_learned(self):
        self.p.propose_learning("u1", "apri spotify", "OPEN_APP", {})
        self.assertEqual(self.p.pending_learning_count, 1)


if __name__ == "__main__":
    unittest.main()
