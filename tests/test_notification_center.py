"""Test unitari per il sistema di modalita' di notifica (v4.3, core/notification_center.py)."""
import unittest

from core.notification_center import NotificationCenter, NotificationMode


class GateTests(unittest.TestCase):
    def test_normal_mode_lets_everything_through(self):
        center = NotificationCenter()
        self.assertEqual(center.gate("reminder", "promemoria"), "promemoria")
        self.assertEqual(center.gate("advisory", "avviso"), "avviso")
        self.assertEqual(center.gate("trigger", "automazione"), "automazione")
        self.assertEqual(center.pending_count(), 0)

    def test_do_not_disturb_suppresses_advisory_and_trigger_but_not_reminder(self):
        center = NotificationCenter(mode=NotificationMode.DO_NOT_DISTURB)
        self.assertEqual(center.gate("reminder", "prendi la medicina"), "prendi la medicina")
        self.assertIsNone(center.gate("advisory", "batteria scarica"))
        self.assertIsNone(center.gate("trigger", "backup fatto"))
        self.assertEqual(center.pending_count(), 2)

    def test_meeting_suppresses_everything_including_reminders(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        self.assertIsNone(center.gate("reminder", "chiamata alle 15"))
        self.assertIsNone(center.gate("advisory", "batteria scarica"))
        self.assertIsNone(center.gate("trigger", "backup fatto"))
        self.assertEqual(center.pending_count(), 3)

    def test_gaming_lets_reminders_and_triggers_through_but_not_advisories(self):
        center = NotificationCenter(mode=NotificationMode.GAMING)
        self.assertEqual(center.gate("reminder", "pausa"), "pausa")
        self.assertEqual(center.gate("trigger", "download completato"), "download completato")
        self.assertIsNone(center.gate("advisory", "disco quasi pieno"))

    def test_empty_message_is_never_queued(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        self.assertIsNone(center.gate("advisory", ""))
        self.assertEqual(center.pending_count(), 0)


class SetModeAndReleaseTests(unittest.TestCase):
    def test_returning_to_normal_releases_all_queued_messages_in_order(self):
        center = NotificationCenter(mode=NotificationMode.DO_NOT_DISTURB)
        center.gate("advisory", "primo avviso")
        center.gate("trigger", "prima automazione")

        released = center.set_mode(NotificationMode.NORMAL)

        self.assertEqual(released, ["primo avviso", "prima automazione"])
        self.assertEqual(center.pending_count(), 0)

    def test_switching_between_two_restrictive_modes_keeps_still_unreleased_items_queued(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        center.gate("reminder", "promemoria durante la riunione")

        released = center.set_mode(NotificationMode.DO_NOT_DISTURB)

        # DO_NOT_DISTURB ammette i promemoria: quello in coda viene rilasciato.
        self.assertEqual(released, ["promemoria durante la riunione"])
        self.assertEqual(center.pending_count(), 0)

    def test_partial_release_leaves_the_rest_queued(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        center.gate("reminder", "promemoria")
        center.gate("advisory", "avviso")

        released = center.set_mode(NotificationMode.DO_NOT_DISTURB)

        self.assertEqual(released, ["promemoria"])
        self.assertEqual(center.pending_count(), 1)  # l'avviso resta in coda, DND non lo ammette


if __name__ == "__main__":
    unittest.main()
