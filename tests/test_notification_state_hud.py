"""F4.5.6: durante "non disturbare" le notifiche trattenute erano invisibili nell'HUD. Ora modalita' e quante
aspettano arrivano come NOTIFICATION_STATE (solo quando cambiano, mai i testi). NotificationCenter, EventBus e
riduttore reali; stessa pipeline di JakeCore.notify."""
import json
import unittest
from unittest import mock

from core.event_bus import EventBus
from core.hud_view_state import HudViewState
from core.notification_center import NotificationCenter
from skills.notification_mode import SetNotificationModeSkill


class NotificationStateHudTests(unittest.TestCase):
    def test_held_back_notifications_are_counted_in_the_hud_without_their_text(self):
        from core.jake_core import JakeCore

        core = JakeCore.__new__(JakeCore)
        core.event_bus = EventBus()
        core.logger = mock.MagicMock()
        core.notification_center = NotificationCenter()
        core.notification_center.on_state_change = core._publish_notification_state
        events = core.event_bus.subscribe()

        SetNotificationModeSkill(core.notification_center).execute({"mode": "do_not_disturb"})
        self.assertIsNone(core.notify("advisory", "Batteria al 12%"))
        self.assertIsNone(core.notify("trigger", "Backup completato"))
        core.notification_center.set_mode(core.notification_center.mode)  # nessun cambiamento: nessun evento in piu'

        view, published = HudViewState(), []
        view.connection_started()
        while not events.empty():
            event = json.loads(events.get_nowait().to_json())
            published.append(event)
            view.apply(event)
        self.assertEqual([e["payload"]["pending"] for e in published], [0, 1, 2])
        self.assertEqual((view.notification_mode_label, view.notifications_pending), ("non disturbare", 2))
        self.assertNotIn("Batteria", json.dumps(published))


if __name__ == "__main__":
    unittest.main()
