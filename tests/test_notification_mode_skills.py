"""Test unitari per SET_NOTIFICATION_MODE/GET_NOTIFICATION_MODE (skills/notification_mode.py,
v4.3) e per JakeCore.notify(), il punto unico da cui passano promemoria/avvisi/automazioni prima
di essere presentati in CLI o in voce."""
import unittest

from core.event_bus import EventBus
from core.jake_core import JakeCore
from core.notification_center import NotificationCenter, NotificationMode
from skills.notification_mode import GetNotificationModeSkill, SetNotificationModeSkill


class SetNotificationModeSkillTests(unittest.TestCase):
    def test_valid_mode_is_applied(self):
        center = NotificationCenter()
        skill = SetNotificationModeSkill(center)
        result = skill.execute({"mode": "gaming"})
        self.assertTrue(result.success)
        self.assertEqual(center.mode, NotificationMode.GAMING)
        self.assertEqual(result.data["mode"], "gaming")

    def test_invalid_mode_is_rejected(self):
        center = NotificationCenter()
        skill = SetNotificationModeSkill(center)
        result = skill.execute({"mode": "party"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "INVALID_VALUE")
        self.assertEqual(center.mode, NotificationMode.NORMAL, "una modalita' non valida non deve cambiare nulla")

    def test_released_messages_are_reported(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        center.gate("reminder", "chiamare il dentista")
        skill = SetNotificationModeSkill(center)

        result = skill.execute({"mode": "normal"})

        self.assertEqual(result.data["released"], ["chiamare il dentista"])


class GetNotificationModeSkillTests(unittest.TestCase):
    def test_reports_current_mode_and_pending_count(self):
        center = NotificationCenter(mode=NotificationMode.STUDY)
        center.gate("advisory", "batteria scarica")
        skill = GetNotificationModeSkill(center)

        result = skill.execute({})

        self.assertEqual(result.data["mode"], "study")
        self.assertEqual(result.data["pending"], 1)


class JakeCoreNotifyTests(unittest.TestCase):
    def _bare_core(self, mode=NotificationMode.NORMAL) -> JakeCore:
        core = JakeCore.__new__(JakeCore)
        core.notification_center = NotificationCenter(mode=mode)
        core.event_bus = EventBus()  # v4.9.1: notify() pubblica anche sul bus eventi
        return core

    def test_normal_mode_passes_the_message_through(self):
        core = self._bare_core()
        self.assertEqual(core.notify("advisory", "batteria scarica"), "batteria scarica")

    def test_restrictive_mode_queues_instead_of_returning(self):
        core = self._bare_core(mode=NotificationMode.MEETING)
        self.assertIsNone(core.notify("reminder", "prendi la medicina"))
        self.assertEqual(core.notification_center.pending_count(), 1)


if __name__ == "__main__":
    unittest.main()
