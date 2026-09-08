"""Test unitari per la pubblicazione di eventi da JakeCore sul bus condiviso (v4.9.1, HUD IPC
transport): _on_agent_step -> AGENT_STEP, notify() -> NOTIFICATION. Vedi tests/test_privacy.py
per answer() -> USER_MESSAGE/JAKE_MESSAGE/ERROR (li' e' gia' testata anche l'interazione con la
modalita' privata) e tests/test_companion_server.py per il trasporto in rete."""
import unittest

from core.event_bus import EventBus
from core.hud_protocol import EventType
from core.jake_core import JakeCore
from core.notification_center import NotificationCenter, NotificationMode


def _bare_core(mode=NotificationMode.NORMAL) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.event_bus = EventBus()
    core.notification_center = NotificationCenter(mode=mode)
    core.session_hooks = type("Hooks", (), {"call": lambda self, *a, **kw: None})()
    return core


class AgentStepEventTests(unittest.TestCase):
    def test_on_agent_step_publishes_agent_step_event(self):
        core = _bare_core()
        subscriber = core.event_bus.subscribe()

        core._on_agent_step(2, "Cerco il file tesi.pdf")

        event = subscriber.get_nowait()
        self.assertEqual(event.type, EventType.AGENT_STEP)
        self.assertEqual(event.payload, {"step": 2, "description": "Cerco il file tesi.pdf"})


class NotifyEventTests(unittest.TestCase):
    def test_allowed_notification_is_published(self):
        core = _bare_core(mode=NotificationMode.NORMAL)
        subscriber = core.event_bus.subscribe()

        message = core.notify("advisory", "batteria scarica")

        self.assertEqual(message, "batteria scarica")
        event = subscriber.get_nowait()
        self.assertEqual(event.type, EventType.NOTIFICATION)
        self.assertEqual(event.payload, {"kind": "advisory", "text": "batteria scarica"})

    def test_queued_notification_is_not_published_yet(self):
        """Se la modalita' corrente mette la notifica in coda (v4.3), non deve nemmeno
        comparire sul bus eventi: l'HUD/companion non deve vederla finche' non e' davvero
        ammessa (altrimenti la coda di NotificationCenter perderebbe senso)."""
        core = _bare_core(mode=NotificationMode.MEETING)
        subscriber = core.event_bus.subscribe()

        message = core.notify("advisory", "batteria scarica")

        self.assertIsNone(message)
        self.assertTrue(subscriber.empty())


if __name__ == "__main__":
    unittest.main()
