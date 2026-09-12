"""Test unitari per core/event_bus.py: nessuna suite dedicata esisteva finora (solo test di
integrazione in tests/test_jake_core_event_bus.py, che verificano come JakeCore lo usa, non il
comportamento di EventBus stesso).

F1.8.6 ("impedire che un client lento blocchi event bus o altri client"): il codice sembrava
gia' corretto per costruzione (coda limitata per iscritto, put_nowait/get_nowait, mai un blocco),
ma nessun test lo dimostrava con timing reale - qui si verifica con un cronometro vero, non solo
leggendo il codice, che un iscritto che non legge mai non rallenta ne' publish() ne' un secondo
iscritto che legge normalmente."""
import threading
import time
import unittest

from core.event_bus import EventBus


class SubscribeUnsubscribeTests(unittest.TestCase):
    def test_subscribe_returns_a_working_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.publish("evento")
        self.assertEqual(q.get_nowait(), "evento")

    def test_subscriber_count_reflects_subscriptions(self):
        bus = EventBus()
        self.assertEqual(bus.subscriber_count(), 0)
        q1 = bus.subscribe()
        bus.subscribe()
        self.assertEqual(bus.subscriber_count(), 2)
        bus.unsubscribe(q1)
        self.assertEqual(bus.subscriber_count(), 1)

    def test_unsubscribing_an_unknown_queue_does_not_raise(self):
        bus = EventBus()
        bus.unsubscribe(__import__("queue").Queue())  # mai iscritta

    def test_unsubscribed_queue_stops_receiving_events(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.publish("evento")
        self.assertTrue(q.empty())


class PublishDeliversToAllTests(unittest.TestCase):
    def test_every_subscriber_receives_the_same_event(self):
        bus = EventBus()
        subscribers = [bus.subscribe() for _ in range(5)]
        bus.publish({"type": "TEST"})
        for q in subscribers:
            self.assertEqual(q.get_nowait(), {"type": "TEST"})

    def test_events_are_delivered_in_order(self):
        bus = EventBus()
        q = bus.subscribe()
        for i in range(10):
            bus.publish(i)
        self.assertEqual([q.get_nowait() for _ in range(10)], list(range(10)))

    def test_publishing_with_no_subscribers_does_not_raise(self):
        EventBus().publish("nessuno ascolta")


class SlowSubscriberDoesNotBlockTests(unittest.TestCase):
    """Il cuore di F1.8.6: un iscritto che non legge MAI (client disconnesso, bloccato, di rete
    lento) non deve mai far aspettare chi pubblica ne' rallentare un secondo iscritto sano."""

    def test_a_full_queue_evicts_the_oldest_event_instead_of_blocking(self):
        bus = EventBus(max_queue_size=3)
        q = bus.subscribe()  # non legge mai: si riempie e resta piena
        for i in range(10):
            bus.publish(i)  # non deve mai bloccare, anche a coda piena
        # Gli ultimi 3 pubblicati devono essere quelli rimasti (i piu' vecchi vengono scartati).
        self.assertEqual([q.get_nowait() for _ in range(3)], [7, 8, 9])

    def test_publishing_into_a_saturated_never_draining_subscriber_stays_fast(self):
        """Prova a cronometro, non solo a codice letto: 2000 publish() con un iscritto che non
        legge mai non devono impiegare piu' di una manciata di millisecondi in totale - se
        publish() bloccasse anche solo per un timeout su quell'iscritto, il tempo totale
        esploderebbe."""
        bus = EventBus(max_queue_size=10)
        bus.subscribe()  # mai letta

        start = time.monotonic()
        for i in range(2000):
            bus.publish(i)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 1.0, f"publish() sembra bloccare su un iscritto pieno (elapsed={elapsed:.3f}s)")

    def test_a_healthy_subscriber_keeps_receiving_events_promptly_despite_a_stuck_sibling(self):
        """Non solo 'publish() e' veloce': un SECONDO iscritto che legge normalmente deve
        continuare a ricevere tutto, senza ritardi, anche mentre un iscritto gemello e' saturo e
        non legge mai. max_queue_size ampiamente sopra la raffica pubblicata: la coda del gemello
        sano non deve mai saturarsi per conto suo (dipenderebbe solo da quanto in fretta il
        thread lettore viene schedulato, non dal comportamento di EventBus verso di lui) - la
        sola variabile sotto test e' se il gemello BLOCCATO influenza quello sano, non se una
        coda troppo piccola perderebbe eventi anche da sola."""
        bus = EventBus(max_queue_size=100)
        bus.subscribe()  # il gemello bloccato, mai letto
        healthy = bus.subscribe()

        received = []

        def _drain():
            for _ in range(50):
                received.append(healthy.get(timeout=2))

        drainer = threading.Thread(target=_drain)
        drainer.start()
        for i in range(50):
            bus.publish(i)
        drainer.join(timeout=5)

        self.assertFalse(drainer.is_alive(), "il thread di lettura non ha ricevuto tutti gli eventi in tempo")
        self.assertEqual(received, list(range(50)))


class ConcurrencyTests(unittest.TestCase):
    """Publish/subscribe/unsubscribe da thread diversi, contemporaneamente - lo scenario reale
    (companion server con piu' client SSE, ognuno sul proprio thread, mentre il thread principale
    pubblica eventi di stato)."""

    def test_concurrent_publish_and_subscribe_does_not_crash_or_lose_the_subscriber_list(self):
        bus = EventBus(max_queue_size=50)
        errors = []

        def _publisher():
            for i in range(200):
                try:
                    bus.publish(i)
                except Exception as exc:  # pragma: no cover - fallirebbe il test comunque
                    errors.append(exc)

        def _subscriber_churn():
            for _ in range(200):
                q = bus.subscribe()
                bus.unsubscribe(q)

        threads = [threading.Thread(target=_publisher) for _ in range(4)]
        threads += [threading.Thread(target=_subscriber_churn) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(bus.subscriber_count(), 0, "ogni subscribe() e' stata seguita da un unsubscribe()")


if __name__ == "__main__":
    unittest.main()
