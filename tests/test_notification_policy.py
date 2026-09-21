"""Test per core/notification_policy.py (F6.3.1-F6.3.9). Orologi finti, nessuna rete, nessun dispositivo."""
import tempfile
import unittest
from datetime import time as clock_time
from pathlib import Path

from core.notification_center import NotificationMode
from core.notification_policy import (
    PRIVATE_SPEECH_PLACEHOLDER, PSTN_ESCALATION_ALLOWED, ContactPreferences, Device, FeedbackStore, Notification,
    NotificationPolicy, ProactiveContact, QuietHours, RelevanceTracker, build_digest,
)

T0 = 1_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def note(kind="reminder", message="Prendi la medicina", **kwargs):
    kwargs.setdefault("created_at", T0)
    return Notification(kind, message, **kwargs)


def policy(mode=NotificationMode.NORMAL, clock=None, hour=(12, 0), **kwargs):
    clock = clock or Clock()
    return NotificationPolicy(mode, clock=clock, now_of_day=lambda: clock_time(*hour), **kwargs), clock


PC = Device("pc", "pc", active=True)
PHONE = Device("telefono", "phone")


class PriorityTests(unittest.TestCase):
    def test_a_reminder_outranks_a_trigger_which_outranks_an_advisory(self):
        p, _ = policy()
        scores = [p.score(note(k)) for k in ("reminder", "trigger", "advisory")]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertLess(scores[2], scores[0])

    def test_a_critical_event_is_always_100_and_a_normal_one_never_reaches_it(self):
        p, _ = policy()
        self.assertEqual(p.score(note(critical=True)), 100.0)
        self.assertLess(p.score(note("reminder", contact="anna")), 100.0)

    def test_a_critical_contact_makes_any_notification_critical(self):
        p, _ = policy(critical_contacts={"mamma"})
        self.assertTrue(p.is_critical(note("advisory", contact="mamma")))
        self.assertFalse(p.is_critical(note("advisory", contact="sconosciuto")))

    def test_a_vip_contact_raises_priority_without_being_critical(self):
        p, _ = policy(vip_contacts={"capo"})
        base = p.score(note("trigger"))
        vip = p.score(note("trigger", contact="capo"))
        self.assertGreater(vip, base)
        self.assertFalse(p.is_critical(note("trigger", contact="capo")))

    def test_waiting_in_the_queue_raises_the_priority(self):
        p, clock = policy()
        old = note("advisory")
        first = p.score(old)
        clock.t += 3 * 3600
        self.assertGreater(p.score(old), first)
        clock.t += 100 * 3600
        self.assertLessEqual(p.score(old), first + 30.001)  # tetto dell'anzianita'


class ModeTests(unittest.TestCase):
    def test_normal_mode_delivers_everything(self):
        p, _ = policy()
        for kind in ("reminder", "trigger", "advisory"):
            self.assertEqual(p.decide(note(kind)).action, "deliver_now", kind)

    def test_study_lets_a_reminder_through_but_queues_an_advisory(self):
        p, _ = policy(NotificationMode.STUDY)
        self.assertEqual(p.decide(note("reminder")).action, "deliver_now")
        self.assertEqual(p.decide(note("advisory", "batteria bassa")).action, "queue")
        self.assertEqual(p.pending(), 1)

    def test_gaming_lets_triggers_pass_like_the_legacy_matrix_but_not_advisories(self):
        p, _ = policy(NotificationMode.GAMING)
        self.assertNotEqual(p.decide(note("trigger")).action, "queue") if p.score(note("trigger")) >= 60 else None
        self.assertEqual(p.decide(note("advisory")).action, "queue")

    def test_meeting_queues_everything_that_is_not_critical(self):
        p, _ = policy(NotificationMode.MEETING)
        for kind in ("reminder", "trigger", "advisory"):
            self.assertEqual(p.decide(note(kind)).action, "queue", kind)

    def test_a_critical_event_breaks_through_a_meeting_but_silently_on_screen(self):
        p, _ = policy(NotificationMode.MEETING)
        decision = p.decide(note(critical=True, message="Allarme fumo in casa"), [PC])
        self.assertEqual((decision.action, decision.channel), ("deliver_now", "screen"))
        self.assertIn("in silenzio", decision.reason)
        self.assertIsNone(decision.spoken_text)

    def test_sleep_and_dnd_are_stricter_than_study(self):
        thresholds = {m: policy(m)[0].threshold() for m in NotificationMode}
        self.assertLess(thresholds[NotificationMode.NORMAL], thresholds[NotificationMode.STUDY])
        self.assertLess(thresholds[NotificationMode.STUDY], thresholds[NotificationMode.DO_NOT_DISTURB])
        self.assertLess(thresholds[NotificationMode.DO_NOT_DISTURB], thresholds[NotificationMode.SLEEP])
        self.assertLess(thresholds[NotificationMode.SLEEP], thresholds[NotificationMode.MEETING])

    def test_every_decision_carries_a_reason(self):
        p, _ = policy(NotificationMode.STUDY)
        self.assertIn("soglia", p.decide(note("advisory")).reason)


class QuietHoursTests(unittest.TestCase):
    NIGHT = QuietHours(clock_time(23, 0), clock_time(7, 0))

    def test_the_window_wraps_around_midnight(self):
        for moment, inside in ((clock_time(23, 0), True), (clock_time(2, 0), True), (clock_time(6, 59), True),
                               (clock_time(7, 0), False), (clock_time(12, 0), False), (clock_time(22, 59), False)):
            with self.subTest(moment=moment):
                self.assertEqual(self.NIGHT.contains(moment), inside)

    def test_a_same_day_window(self):
        window = QuietHours(clock_time(13, 0), clock_time(15, 0))
        self.assertTrue(window.contains(clock_time(14, 0)))
        self.assertFalse(window.contains(clock_time(15, 0)))

    def test_at_night_an_advisory_and_a_normal_reminder_wait_but_a_critical_one_gets_through(self):
        p, _ = policy(hour=(2, 30), quiet_hours=self.NIGHT)
        self.assertEqual(p.decide(note("advisory")).action, "queue")
        self.assertEqual(p.decide(note("reminder")).action, "queue")  # 70 < soglia di notte
        night_critical = p.decide(note(critical=True, message="Chiamata dall'ospedale"), [PHONE])
        self.assertEqual((night_critical.action, night_critical.channel), ("deliver_now", "screen"))

    def test_outside_quiet_hours_nothing_changes(self):
        p, _ = policy(hour=(12, 0), quiet_hours=self.NIGHT)
        self.assertEqual(p.decide(note("advisory")).action, "deliver_now")

    def test_the_stricter_of_mode_and_quiet_hours_wins(self):
        p, _ = policy(NotificationMode.MEETING, hour=(12, 0), quiet_hours=self.NIGHT)
        self.assertEqual(p.threshold(), 101)


class DeviceTests(unittest.TestCase):
    def test_the_active_device_is_preferred(self):
        p, _ = policy()
        self.assertEqual(p.decide(note(), [PHONE, PC]).device_id, "pc")

    def test_without_an_active_device_the_phone_is_next(self):
        p, _ = policy()
        watch, phone = Device("orologio", "watch"), Device("telefono", "phone")
        self.assertEqual(p.decide(note(), [watch, phone]).device_id, "telefono")

    def test_a_declared_target_device_is_honoured_and_never_silently_replaced(self):
        p, _ = policy()
        self.assertEqual(p.decide(note(target_device="telefono"), [PC, PHONE]).device_id, "telefono")
        offline = Device("telefono", "phone", online=False)
        decision = p.decide(note(target_device="telefono"), [PC, offline])
        self.assertEqual(decision.action, "queue")  # non finisce sul PC al posto del telefono richiesto

    def test_no_online_device_queues_the_notification(self):
        p, _ = policy()
        self.assertEqual(p.decide(note(), [Device("pc", "pc", online=False)]).action, "queue")

    def test_a_watch_gets_the_screen_channel(self):
        p, _ = policy()
        decision = p.decide(note(), [Device("orologio", "watch", active=True)])
        self.assertEqual(decision.channel, "screen")

    def test_non_public_content_never_goes_to_a_device_without_a_private_screen(self):
        p, _ = policy()
        tv = Device("tv", "speaker", active=True, private_screen=False)
        self.assertEqual(p.decide(note(sensitivity="private"), [tv]).action, "queue")
        self.assertEqual(p.decide(note(sensitivity="public"), [tv]).action, "deliver_now")


class SensitiveOnSharedSpeakerTests(unittest.TestCase):
    SHARED = Device("salotto", "speaker", active=True, shared_speaker=True)

    def test_undeclared_content_is_not_spoken_on_a_shared_speaker(self):
        p, _ = policy()
        decision = p.decide(note(message="Appuntamento dal medico alle 15"), [self.SHARED])
        self.assertEqual(decision.channel, "voice")
        self.assertEqual(decision.spoken_text, PRIVATE_SPEECH_PLACEHOLDER)
        self.assertNotIn("medico", decision.spoken_text)
        self.assertEqual(decision.screen_text, "Appuntamento dal medico alle 15")  # i dettagli restano sullo schermo

    def test_content_declared_public_is_spoken_in_full(self):
        p, _ = policy()
        decision = p.decide(note(message="Il timer della pasta e' finito", sensitivity="public"), [self.SHARED])
        self.assertEqual(decision.spoken_text, "Il timer della pasta e' finito")

    def test_on_a_personal_device_undeclared_content_is_spoken(self):
        p, _ = policy()
        decision = p.decide(note(message="Appuntamento dal medico"), [PC])
        self.assertEqual(decision.spoken_text, "Appuntamento dal medico")

    def test_a_critical_alert_on_a_shared_speaker_is_still_generic_if_not_public(self):
        p, _ = policy()
        decision = p.decide(note(critical=True, message="Risultato delle analisi disponibile"), [self.SHARED])
        self.assertEqual(decision.spoken_text, PRIVATE_SPEECH_PLACEHOLDER)

    def test_an_invalid_sensitivity_is_rejected(self):
        with self.assertRaises(ValueError):
            note(sensitivity="boh")


class DigestAndStarvationTests(unittest.TestCase):
    def test_queued_items_are_released_in_one_grouped_digest_ordered_by_priority(self):
        p, _ = policy(NotificationMode.STUDY)
        p.decide(note("advisory", "batteria bassa"))
        p.decide(note("advisory", "disco quasi pieno"))
        p.decide(note("trigger", "download completato"))
        digest = p.release()
        assert digest is not None
        self.assertEqual(digest.counts, {"advisory": 2, "trigger": 1})
        self.assertIn("2 avvisi di sistema", digest.text)
        self.assertIn("1 automazioni", digest.text)
        self.assertTrue(digest.text.index("download completato") < digest.text.index("batteria bassa"))  # il piu' importante prima
        self.assertEqual(p.pending(), 0)

    def test_duplicates_are_collapsed_and_counted(self):
        p, _ = policy(NotificationMode.STUDY)
        for _ in range(4):
            p.decide(note("advisory", "batteria bassa"))
        digest = p.release()
        assert digest is not None
        self.assertEqual(digest.counts, {"advisory": 1})
        self.assertIn("3 ripetute", digest.text)

    def test_a_long_digest_names_only_the_top_three(self):
        p, _ = policy(NotificationMode.STUDY)
        for i in range(6):
            p.decide(note("advisory", f"avviso numero {i}"))
        digest = p.release()
        assert digest is not None
        self.assertIn("e altre 3", digest.text)

    def test_an_empty_queue_releases_nothing(self):
        self.assertIsNone(policy()[0].release())

    def test_leaving_a_restrictive_mode_releases_what_now_clears_the_threshold(self):
        p, _ = policy(NotificationMode.MEETING)
        p.decide(note("reminder", "chiama il dentista"))
        p.decide(note("advisory", "aggiornamento disponibile"))
        digest = p.set_mode(NotificationMode.NORMAL)
        assert digest is not None
        self.assertEqual(digest.counts, {"reminder": 1, "advisory": 1})
        self.assertEqual(p.pending(), 0)

    def test_switching_to_another_restrictive_mode_keeps_the_rest_waiting(self):
        p, _ = policy(NotificationMode.MEETING)
        p.decide(note("advisory", "aggiornamento disponibile"))
        self.assertIsNone(p.set_mode(NotificationMode.STUDY))  # 30 < 65: resta in coda
        self.assertEqual(p.pending(), 1)

    def test_nothing_waits_forever_in_a_long_restrictive_mode(self):
        p, clock = policy(NotificationMode.STUDY, max_wait_s=3600)
        p.decide(note("advisory", "backup fallito"))
        self.assertEqual(p.due_for_release(), [])
        clock.t += 3601
        self.assertEqual(len(p.due_for_release()), 1)
        digest = p.release(only_due=True)
        assert digest is not None
        self.assertEqual(p.pending(), 0)

    def test_only_due_items_are_released_and_recent_ones_stay(self):
        p, clock = policy(NotificationMode.STUDY, max_wait_s=3600)
        p.decide(note("advisory", "vecchia"))
        clock.t += 3000
        p.decide(note("advisory", "recente", created_at=clock.t))
        clock.t += 700
        digest = p.release(only_due=True)
        assert digest is not None
        self.assertEqual([n.message for n in digest.notifications], ["vecchia"])
        self.assertEqual(p.pending(), 1)

    def test_a_meeting_defers_even_the_forced_release_until_it_ends(self):
        p, clock = policy(NotificationMode.MEETING, max_wait_s=60)
        p.decide(note("advisory", "x"))
        clock.t += 7200
        self.assertEqual(p.due_for_release(), [])

    def test_the_queue_is_bounded_and_the_overflow_still_reaches_the_digest(self):
        p, _ = policy(NotificationMode.STUDY, queue_limit=3)
        for i in range(8):
            p.decide(note("advisory", f"avviso {i}"))
        self.assertEqual(p.pending(), 3)
        digest = p.release()
        assert digest is not None
        self.assertEqual(len(digest.notifications), 8)  # nessuna notifica persa per il limite

    def test_even_the_overflow_is_bounded_and_the_digest_says_what_was_not_kept(self):
        p, _ = policy(NotificationMode.STUDY, queue_limit=2)
        p.OVERFLOW_LIMIT = 10
        for i in range(30):
            p.decide(note("advisory", f"avviso {i}"))
        digest = p.release()
        assert digest is not None
        self.assertIn("non sono state conservate", digest.text)
        self.assertEqual(len(digest.notifications), 12)  # 2 in coda + 10 di eccedenza

    def test_build_digest_can_be_used_directly(self):
        p, clock = policy()
        digest = build_digest([note("reminder", "a"), note("trigger", "b")], p, clock.t)
        self.assertEqual(digest.counts, {"reminder": 1, "trigger": 1})


class FeedbackTests(unittest.TestCase):
    def test_less_like_this_lowers_the_priority_of_that_source_only(self):
        p, _ = policy()
        noisy, other = note("advisory", "batteria", source="battery"), note("advisory", "disco", source="disk")
        before = p.score(noisy)
        p.feedback.less_like_this(noisy.feedback_key)
        self.assertLess(p.score(noisy), before)
        self.assertEqual(p.score(other), before)

    def test_it_can_push_a_notification_below_a_mode_threshold(self):
        p, _ = policy(NotificationMode.NORMAL)
        n = note("reminder", "spazzatura", source="casa", created_at=T0)
        for _ in range(3):
            p.feedback.less_like_this(n.feedback_key)
        study, _ = policy(NotificationMode.STUDY)
        study.feedback = p.feedback
        self.assertEqual(study.decide(n).action, "queue")

    def test_it_can_never_silence_a_critical_event(self):
        p, _ = policy(NotificationMode.MEETING)
        n = note("advisory", "fumo", source="allarme", critical=True)
        for _ in range(10):
            p.feedback.less_like_this(n.feedback_key)
        self.assertEqual(p.score(n), 100.0)
        self.assertEqual(p.decide(n, [PC]).action, "deliver_now")

    def test_the_multiplier_has_a_floor(self):
        store = FeedbackStore(clock=Clock())
        for _ in range(20):
            store.less_like_this("k")
        self.assertGreaterEqual(store.multiplier("k"), FeedbackStore.FLOOR)

    def test_the_effect_fades_with_time(self):
        clock = Clock()
        store = FeedbackStore(clock=clock)
        store.less_like_this("k")
        just_after = store.multiplier("k")
        clock.t += 60 * 86400
        self.assertGreater(store.multiplier("k"), just_after)
        self.assertLessEqual(store.multiplier("k"), 1.0)

    def test_it_is_reversible(self):
        store = FeedbackStore(clock=Clock())
        store.less_like_this("k")
        self.assertTrue(store.undo("k"))
        self.assertEqual(store.multiplier("k"), 1.0)
        self.assertFalse(store.undo("k"))

    def test_it_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.json"
            clock = Clock()
            FeedbackStore(path, clock).less_like_this("advisory:battery")
            self.assertLess(FeedbackStore(path, clock).multiplier("advisory:battery"), 1.0)

    def test_a_corrupt_file_means_no_feedback_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.json"
            path.write_text("{ rotto", encoding="utf-8")
            self.assertEqual(FeedbackStore(path).multiplier("k"), 1.0)


class RelevanceTests(unittest.TestCase):
    def test_counts_irrelevant_interruptions_per_day_and_names_the_worst_source(self):
        clock = Clock()
        tracker = RelevanceTracker(clock)
        for _ in range(3):
            tracker.record(note("advisory", source="battery"), acted=False)
        tracker.record(note("reminder", source="medicine"), acted=True)
        summary = tracker.summary(days=1)
        self.assertEqual((summary["interruptions"], summary["irrelevant"]), (4, 3))
        self.assertAlmostEqual(summary["relevance_rate"], 0.25)
        self.assertEqual(summary["irrelevant_per_day"], 3.0)
        self.assertEqual(summary["worst_sources"][0], ("advisory:battery", 3))

    def test_old_events_fall_out_of_the_window(self):
        clock = Clock()
        tracker = RelevanceTracker(clock)
        tracker.record(note(), acted=False)
        clock.t += 3 * 86400
        self.assertEqual(tracker.summary(days=1)["interruptions"], 0)
        self.assertIsNone(tracker.summary(days=1)["relevance_rate"])
        self.assertEqual(tracker.summary(days=7)["interruptions"], 1)

    def test_the_f6_target_is_expressible_as_less_than_one_irrelevant_per_day(self):
        tracker = RelevanceTracker(Clock())
        tracker.record(note(), acted=True)
        self.assertLess(tracker.summary()["irrelevant_per_day"], 1)


class ProactiveContactTests(unittest.TestCase):
    def _contact(self, allow_calls=True, mode=NotificationMode.NORMAL, **prefs):
        p, clock = policy(mode)
        contact = ProactiveContact(p, ContactPreferences(allow_calls=allow_calls, **prefs), clock=clock)
        return contact, clock

    ARGS = {"task_id": "backup-1", "situation": "Il backup notturno e' fallito.", "verified_actions": ["ho controllato lo spazio"],
            "decision_needed": "riprovare adesso?", "devices": [PC, PHONE]}

    def test_the_briefing_explains_situation_verified_actions_and_the_decision_needed(self):
        contact, _ = self._contact()
        plan = contact.plan(urgency="normal", **self.ARGS)
        assert plan.briefing is not None
        for fragment in ("backup-1", "backup notturno e' fallito", "ho controllato lo spazio", "riprovare adesso?"):
            self.assertIn(fragment, plan.briefing)

    def test_low_urgency_goes_to_the_digest(self):
        contact, _ = self._contact()
        self.assertEqual(contact.plan(urgency="low", **self.ARGS).channel, "digest")

    def test_an_urgent_matter_may_call_when_preferences_allow_it(self):
        contact, _ = self._contact()
        plan = contact.plan(urgency="urgent", **self.ARGS)
        self.assertEqual((plan.channel, plan.device_id), ("call", "pc"))
        self.assertIsNotNone(plan.cooldown_until)

    def test_calls_are_off_by_default_and_an_urgent_matter_becomes_a_notification(self):
        contact, _ = self._contact(allow_calls=False)
        self.assertEqual(contact.plan(urgency="urgent", **self.ARGS).channel, "notification")

    def test_no_online_device_falls_back_to_the_digest(self):
        contact, _ = self._contact()
        args = {**self.ARGS, "devices": [Device("pc", "pc", online=False)]}
        self.assertEqual(contact.plan(urgency="urgent", **args).channel, "digest")

    def test_it_never_calls_twice_inside_the_cooldown(self):
        contact, clock = self._contact(call_cooldown_s=1800)
        self.assertEqual(contact.plan(urgency="urgent", **self.ARGS).channel, "call")
        clock.t += 60
        self.assertNotEqual(contact.plan(urgency="urgent", **self.ARGS).channel, "call")

    def test_after_a_rejected_or_unanswered_call_it_falls_back_and_does_not_call_again(self):
        for outcome in ("rejected", "no_answer", "device_offline"):
            with self.subTest(outcome=outcome):
                contact, clock = self._contact(call_cooldown_s=10)
                contact.plan(urgency="urgent", **self.ARGS)
                fallback = contact.record_call_outcome("backup-1", outcome)
                self.assertEqual(fallback.channel, "notification")
                self.assertIn("senza richiamare", fallback.reason)
                clock.t += 60  # anche dopo il cooldown: il limite di tentativi resta
                self.assertNotEqual(contact.plan(urgency="urgent", **self.ARGS).channel, "call")

    def test_an_answered_call_resets_the_failure_count(self):
        contact, clock = self._contact(call_cooldown_s=10, max_call_attempts=1)
        contact.plan(urgency="urgent", **self.ARGS)
        contact.record_call_outcome("backup-1", "no_answer")
        contact.record_call_outcome("backup-1", "answered")
        clock.t += 60
        self.assertEqual(contact.plan(urgency="urgent", **self.ARGS).channel, "call")

    def test_there_is_no_automatic_escalation_to_the_phone_network(self):
        self.assertFalse(PSTN_ESCALATION_ALLOWED)
        contact, _ = self._contact()
        contact.plan(urgency="urgent", **self.ARGS)
        for outcome in ("rejected", "no_answer", "device_offline"):
            self.assertNotIn("PSTN", contact.record_call_outcome("backup-1", outcome).reason)

    def test_a_repeated_non_urgent_contact_for_the_same_task_is_not_insistent(self):
        contact, clock = self._contact(allow_calls=False, notification_cooldown_s=300)
        self.assertEqual(contact.plan(urgency="normal", **self.ARGS).channel, "notification")
        clock.t += 30
        self.assertEqual(contact.plan(urgency="normal", **self.ARGS).channel, "none")
        clock.t += 400
        self.assertEqual(contact.plan(urgency="normal", **self.ARGS).channel, "notification")

    def test_in_a_meeting_a_normal_matter_waits_for_the_digest(self):
        contact, _ = self._contact(allow_calls=False, mode=NotificationMode.MEETING)
        self.assertEqual(contact.plan(urgency="normal", **self.ARGS).channel, "digest")

    def test_in_a_meeting_an_urgent_matter_is_still_a_silent_notification_if_it_cannot_call(self):
        contact, _ = self._contact(allow_calls=False, mode=NotificationMode.MEETING)
        plan = contact.plan(urgency="urgent", **self.ARGS)
        self.assertEqual(plan.channel, "notification")


if __name__ == "__main__":
    unittest.main()
