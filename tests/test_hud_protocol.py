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

    def test_sequence_id_defaults_to_zero_before_publishing(self):
        """F4.1.1: 0 non e' un evento fantasma - e' 'mai passato da EventBus.publish()', l'unico
        punto che assegna un valore vero (vedi EventBusTests sotto)."""
        self.assertEqual(HudEvent(type=EventType.IDLE).sequence_id, 0)

    def test_sequence_id_round_trips_through_json(self):
        event = HudEvent(type=EventType.IDLE)
        event.sequence_id = 42
        self.assertEqual(HudEvent.from_json(event.to_json()).sequence_id, 42)

    def test_from_json_defaults_sequence_id_to_zero_for_a_record_without_it(self):
        """Compatibilita' con un record scritto prima di F4.1.1 (nessuna chiave sequence_id)."""
        restored = HudEvent.from_json('{"type": "IDLE", "payload": {}, "at": 1}')
        self.assertEqual(restored.sequence_id, 0)


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


class EventBusSequenceIdTests(unittest.TestCase):
    """F4.1.1 ("aggiungere sequence id"): un client (HUD nativo, companion) deve poter accorgersi
    di un evento perso o riordinato confrontando due sequence_id consecutivi - impossibile senza
    un numero d'ordine GLOBALE assegnato da un punto unico, non da ciascun produttore per conto
    proprio (due produttori diversi che contassero ciascuno per se' potrebbero assegnare lo stesso
    numero a due eventi diversi)."""

    def test_publish_assigns_increasing_sequence_ids_starting_at_one(self):
        bus = EventBus()
        subscriber = bus.subscribe()
        for _ in range(3):
            bus.publish(HudEvent(type=EventType.IDLE))

        sequence_ids = [subscriber.get_nowait().sequence_id for _ in range(3)]
        self.assertEqual(sequence_ids, [1, 2, 3])

    def test_the_same_event_gets_the_same_sequence_id_for_every_subscriber(self):
        bus = EventBus()
        first, second = bus.subscribe(), bus.subscribe()
        bus.publish(HudEvent(type=EventType.IDLE))

        self.assertEqual(first.get_nowait().sequence_id, second.get_nowait().sequence_id)

    def test_sequence_ids_are_global_not_per_call_site(self):
        """Due 'produttori' diversi (es. JakeCore e companion_server, qui solo due variabili
        distinte che chiamano publish() sullo stesso bus) devono condividere lo STESSO contatore -
        mai due contatori indipendenti che potrebbero assegnare lo stesso numero a due eventi
        diversi."""
        bus = EventBus()
        producer_a_event = HudEvent(type=EventType.USER_MESSAGE)
        producer_b_event = HudEvent(type=EventType.JAKE_MESSAGE)

        bus.publish(producer_a_event)
        bus.publish(producer_b_event)

        self.assertEqual(producer_a_event.sequence_id, 1)
        self.assertEqual(producer_b_event.sequence_id, 2)

    def test_concurrent_publishers_never_assign_a_duplicate_or_skipped_sequence_id(self):
        """Buco plausibile con un contatore non protetto da lock: due thread che leggono lo
        stesso valore e lo incrementano ciascuno per conto proprio produrrebbero un ID duplicato.
        Thread veri (stesso principio gia' usato altrove in questa sessione per i buchi di
        concorrenza), non solo letto a codice."""
        import threading

        bus = EventBus(max_queue_size=1000)
        subscriber = bus.subscribe()
        thread_count, events_per_thread = 8, 50

        def _publish_many():
            for _ in range(events_per_thread):
                bus.publish(HudEvent(type=EventType.IDLE))

        threads = [threading.Thread(target=_publish_many) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        sequence_ids = sorted(subscriber.get_nowait().sequence_id for _ in range(thread_count * events_per_thread))
        self.assertEqual(sequence_ids, list(range(1, thread_count * events_per_thread + 1)))


if __name__ == "__main__":
    unittest.main()
