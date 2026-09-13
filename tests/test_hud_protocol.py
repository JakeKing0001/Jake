"""Test unitari per il protocollo eventi (v4.9.1, core/hud_protocol.py) e il bus multi-
consumatore (core/event_bus.py). Pura logica, nessuna rete."""
import json
import unittest

from core.event_bus import EventBus
from core.hud_protocol import EventType, HudEvent
from core.version import PROTOCOL_VERSION


class HudEventSerializationTests(unittest.TestCase):
    def test_round_trips_through_json(self):
        event = HudEvent(type=EventType.THINKING, payload={"detail": "cerco il file"})
        restored = HudEvent.from_json(event.to_json())
        self.assertEqual(restored.type, EventType.THINKING)
        self.assertEqual(restored.payload, {"detail": "cerco il file"})

    def test_to_json_uses_the_plain_string_value_not_the_enum_repr(self):
        event = HudEvent(type=EventType.NOTIFICATION, payload={})
        self.assertIn('"type": "NOTIFICATION"', event.to_json())

    def test_undo_and_verification_events_round_trip_too(self):
        """F1.3.8 ("esporre undo e prove a HUD/companion tramite eventi versionati"): i due tipi
        aggiunti per questo passo seguono lo stesso protocollo di tutti gli altri, nessun
        trattamento speciale."""
        undo = HudEvent(type=EventType.UNDO, payload={"intent": "CREATE_PATH"})
        verification = HudEvent(type=EventType.VERIFICATION, payload={"intent": "CREATE_PATH", "verified": "verified"})
        self.assertEqual(HudEvent.from_json(undo.to_json()).type, EventType.UNDO)
        self.assertEqual(HudEvent.from_json(verification.to_json()).payload, verification.payload)

    def test_to_json_carries_the_shared_protocol_version(self):
        payload = json.loads(HudEvent(type=EventType.IDLE).to_json())
        self.assertEqual(payload["schema_version"], PROTOCOL_VERSION)

    def test_from_json_rejects_an_unsupported_protocol_version(self):
        with self.assertRaisesRegex(ValueError, "versione protocollo non supportata"):
            HudEvent.from_json(
                '{"schema_version": 999, "type": "IDLE", "payload": {}, "at": 1}'
            )

    def test_from_json_rejects_an_unknown_type(self):
        with self.assertRaises(ValueError):
            HudEvent.from_json('{"type": "NOT_A_REAL_EVENT", "payload": {}}')


class LegacyStateMappingTests(unittest.TestCase):
    def test_known_legacy_states_map_to_an_event(self):
        for state, expected_type in (
            ("idle", EventType.IDLE), ("listening", EventType.LISTENING),
            ("thinking", EventType.THINKING), ("working", EventType.EXECUTING),
            ("speaking", EventType.JAKE_MESSAGE), ("notify", EventType.NOTIFICATION),
            ("error", EventType.ERROR), ("paused", EventType.PAUSED),
        ):
            event = HudEvent.from_legacy_state(state)
            self.assertIsNotNone(event, state)
            self.assertEqual(event.type, expected_type, state)

    def test_unknown_legacy_state_returns_none(self):
        self.assertIsNone(HudEvent.from_legacy_state("qualcosa_di_inventato"))

    def test_detail_is_carried_into_the_payload(self):
        event = HudEvent.from_legacy_state("notify", "batteria scarica")
        self.assertEqual(event.payload, {"detail": "batteria scarica"})

    def test_empty_detail_produces_an_empty_payload(self):
        event = HudEvent.from_legacy_state("idle", "")
        self.assertEqual(event.payload, {})


class EventBusTests(unittest.TestCase):
    def test_published_event_reaches_a_subscriber(self):
        bus = EventBus()
        subscriber = bus.subscribe()
        event = HudEvent(type=EventType.IDLE)

        bus.publish(event)

        self.assertIs(subscriber.get_nowait(), event)

    def test_published_event_reaches_all_subscribers(self):
        bus = EventBus()
        first, second = bus.subscribe(), bus.subscribe()
        event = HudEvent(type=EventType.THINKING)

        bus.publish(event)

        self.assertIs(first.get_nowait(), event)
        self.assertIs(second.get_nowait(), event)

    def test_unsubscribed_queue_no_longer_receives_events(self):
        bus = EventBus()
        subscriber = bus.subscribe()
        bus.unsubscribe(subscriber)

        bus.publish(HudEvent(type=EventType.IDLE))

        self.assertTrue(subscriber.empty())
        self.assertEqual(bus.subscriber_count(), 0)

    def test_full_queue_drops_the_oldest_event_instead_of_blocking(self):
        bus = EventBus(max_queue_size=2)
        subscriber = bus.subscribe()
        events = [HudEvent(type=EventType.IDLE, payload={"i": i}) for i in range(3)]

        for event in events:
            bus.publish(event)

        self.assertEqual(subscriber.qsize(), 2)
        remaining = [subscriber.get_nowait().payload["i"], subscriber.get_nowait().payload["i"]]
        self.assertEqual(remaining, [1, 2], "il piu' vecchio (i=0) doveva essere scartato")

    def test_subscriber_count_reflects_active_subscriptions(self):
        bus = EventBus()
        self.assertEqual(bus.subscriber_count(), 0)
        bus.subscribe()
        bus.subscribe()
        self.assertEqual(bus.subscriber_count(), 2)


if __name__ == "__main__":
    unittest.main()
