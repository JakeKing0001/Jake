"""F7.4 (un solo active responder): con un telefono attivo le notifiche proattive le riceve lui, non anche il PC.

Prima: un promemoria o un avviso usciva sia sul telefono (evento NOTIFICATION sul bus, che lo stream del companion
consegna) sia a voce/in CLI sul PC - doppia risposta, e voce in una stanza magari vuota. Server companion vero (il suo
DeviceRegistry con il lease), JakeCore reale per il percorso di notify()."""
import logging
import unittest

from core.companion_server import CompanionServer
from core.event_bus import EventBus
from core.hud_protocol import EventType
from core.jake_core import JakeCore
from core.notification_center import NotificationCenter


class NotificationResponderTests(unittest.TestCase):
    def setUp(self):
        self.core = JakeCore.__new__(JakeCore)
        self.core.notification_center = NotificationCenter()
        self.core.proactive_gate = None
        self.core.event_bus = EventBus()
        self.core.logger = logging.getLogger("test.notification_responder")
        self.core.ready_for_notifications = True
        self.server = CompanionServer(event_bus=self.core.event_bus)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.core.companion_server = self.server
        self.events = self.core.event_bus.subscribe()

    def _notifications(self) -> list:
        found = []
        while not self.events.empty():
            event = self.events.get_nowait()
            if event.type == EventType.NOTIFICATION:
                found.append(event.payload)
        return found

    def test_with_a_phone_active_the_reminder_goes_to_the_phone_only(self):
        self.server.devices.claim("phone-1", "Telefono")

        shown_on_the_pc = self.core.notify("reminder", "Promemoria: chiamare Marta")

        self.assertIsNone(shown_on_the_pc, "il PC non la dice ne' la stampa")
        self.assertEqual(self._notifications(),
                         [{"kind": "reminder", "text": "Promemoria: chiamare Marta", "responder": "phone-1"}])
        self.assertEqual(self.core.last_notification["message"], "Promemoria: chiamare Marta",
                         "'meno notifiche cosi'' detto dal telefono si riferisce ancora a lei")

    def test_back_on_the_pc_notifications_are_presented_there_again(self):
        self.server.devices.claim("phone-1", "Telefono")
        self.server.devices.release("phone-1")

        self.assertEqual(self.core.notify("reminder", "Promemoria: chiamare Marta"), "Promemoria: chiamare Marta")
        self.assertEqual(self._notifications(), [{"kind": "reminder", "text": "Promemoria: chiamare Marta"}])


if __name__ == "__main__":
    unittest.main()
