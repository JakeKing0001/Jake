"""F6.3.4: "meno notifiche cosi'", "non mostrarmelo piu'", "mostramelo di nuovo" e "rimandala" agiscono sull'ultima
notifica proattiva mostrata, attraverso la stessa pipeline di JakeCore.notify. Prima FeedbackStore esisteva ma
nessuna notifica reale lo consultava."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core.event_bus import EventBus
from core.notification_center import NotificationCenter
from core.notification_policy import FeedbackStore, NotificationPolicy
from core.proactive_gate import ProactiveGate, notification_key
from skills.notification_feedback import (LessNotificationsLikeThisSkill, MuteNotificationSkill, SnoozeNotificationSkill,
                                          UnmuteNotificationSkill)


class NotificationFeedbackTests(unittest.TestCase):
    def setUp(self):
        from core.jake_core import JakeCore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "feedback.json"
        core = JakeCore.__new__(JakeCore)
        core.notification_center = NotificationCenter()
        core.event_bus = EventBus()
        core.logger = mock.MagicMock()
        core.notification_policy = NotificationPolicy(feedback=FeedbackStore(self.path))
        core.proactive_gate = ProactiveGate(dedup_window_s=0, hourly_budget=100, feedback=core.notification_policy.feedback)
        core.last_notification = None
        self.core = core

    def test_the_type_of_a_notification_ignores_the_numbers(self):
        self.assertEqual(notification_key("advisory", "Batteria al 12%"), notification_key("advisory", "Batteria  al 9%"))

    def test_never_again_silences_that_type_until_it_is_restored_and_survives_a_restart(self):
        self.assertEqual(self.core.notify("advisory", "Batteria al 12%"), "Batteria al 12%")
        self.assertTrue(MuteNotificationSkill(self.core).execute().success)
        self.assertIsNone(self.core.notify("advisory", "Batteria al 9%"))
        self.assertEqual(self.core.notification_center.pending_count(), 0, "silenziata, non rimandata")
        self.assertTrue(FeedbackStore(self.path).is_muted(notification_key("advisory", "Batteria al 3%")), "persistente")
        self.assertEqual(self.core.notify("advisory", "Disco quasi pieno"), "Disco quasi pieno", "altri tipi non toccati")
        self.core.last_notification = {"kind": "advisory", "message": "Batteria al 9%", "key": notification_key("advisory", "Batteria al 9%")}
        self.assertTrue(UnmuteNotificationSkill(self.core).execute().success)
        self.assertEqual(self.core.notify("advisory", "Batteria al 5%"), "Batteria al 5%")

    def test_less_like_this_moves_that_type_to_the_digest(self):
        self.core.notify("trigger", "Ho eseguito il backup")
        self.assertTrue(LessNotificationsLikeThisSkill(self.core).execute().success)
        self.assertIsNone(self.core.notify("trigger", "Ho eseguito il backup"))
        self.assertEqual(self.core.notification_center.pending_count(), 1, "rimandata al riepilogo, non persa")

    def test_reminders_cannot_be_silenced_from_here_and_snooze_waits(self):
        self.core.notify("reminder", "Promemoria: medicina")
        self.assertEqual(MuteNotificationSkill(self.core).execute().error, "REMINDER_NOT_MUTABLE")
        self.assertTrue(SnoozeNotificationSkill(self.core).execute({"minutes": 30}).success)
        self.assertEqual(self.core.notification_center.take_deferred(), [], "non prima di 30 minuti")
        with mock.patch("time.time", return_value=time.time() + 31 * 60):
            self.assertEqual([i["message"] for i in self.core.notification_center.take_deferred()], ["Promemoria: medicina"])

    def test_without_a_recent_notification_nothing_happens(self):
        self.assertEqual(MuteNotificationSkill(self.core).execute().error, "NO_RECENT_NOTIFICATION")


if __name__ == "__main__":
    unittest.main()
