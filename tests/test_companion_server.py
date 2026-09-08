"""Test unitari per il server companion (v5.8/5.9/4.9.1, core/companion_server.py). A differenza
di NestClient/OllamaClient (dove Jake e' il CLIENTE e si mocka il lato remoto), qui Jake e' il
SERVER: i test avviano un'istanza vera su una porta effimera (127.0.0.1, port=0) e fanno vere
richieste HTTP con urllib, senza bisogno di un telefono o un secondo dispositivo reale."""
import json
import threading
import time
import unittest
from urllib import error, request

from core.companion_server import CompanionServer
from core.event_bus import EventBus
from core.hud_protocol import EventType


def _get(url: str, timeout: float = 5) -> tuple[int, dict]:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(url: str, payload: dict, timeout: float = 5) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class CompanionServerTestCase(unittest.TestCase):
    def setUp(self):
        self.command_calls = []
        self.server = CompanionServer(command_handler=self._fake_answer)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base_url = f"http://127.0.0.1:{self.server.port}"

    def _fake_answer(self, text: str) -> str:
        self.command_calls.append(text)
        return f"risposta a: {text}"


class LifecycleTests(CompanionServerTestCase):
    def test_start_picks_a_real_ephemeral_port(self):
        self.assertGreater(self.server.port, 0)
        self.assertTrue(self.server.running)

    def test_starting_twice_is_a_no_op(self):
        port_before = self.server.port
        self.server.start()
        self.assertEqual(self.server.port, port_before)

    def test_stop_makes_the_server_unreachable(self):
        self.server.stop()
        self.assertFalse(self.server.running)
        with self.assertRaises(error.URLError):
            request.urlopen(f"{self.base_url}/status", timeout=1)


class StatusEndpointTests(CompanionServerTestCase):
    def test_status_reports_ok_and_no_active_device_initially(self):
        status, body = _get(f"{self.base_url}/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNone(body["active_device"])
        self.assertEqual(body["devices"], [])

    def test_unknown_path_is_404(self):
        status, body = _get(f"{self.base_url}/non-esiste")
        self.assertEqual(status, 404)


class CommandEndpointTests(CompanionServerTestCase):
    def test_command_is_forwarded_to_the_handler_and_response_returned(self):
        status, body = _post(f"{self.base_url}/command", {"text": "che ore sono"})
        self.assertEqual(status, 200)
        self.assertEqual(body["response"], "risposta a: che ore sono")
        self.assertEqual(self.command_calls, ["che ore sono"])

    def test_missing_text_is_rejected_without_calling_the_handler(self):
        status, body = _post(f"{self.base_url}/command", {})
        self.assertEqual(status, 400)
        self.assertEqual(self.command_calls, [])

    def test_handle_command_does_not_publish_events_itself(self):
        """USER_MESSAGE/JAKE_MESSAGE sono responsabilita' del command_handler (JakeCore.answer
        nel caso reale, vedi tests/test_jake_core_event_bus.py), non del server: altrimenti,
        collegato a un vero JakeCore, ogni scambio verrebbe pubblicato due volte."""
        import queue

        subscriber = self.server.event_bus.subscribe()
        _post(f"{self.base_url}/command", {"text": "ciao"})

        with self.assertRaises(queue.Empty):
            subscriber.get(timeout=0.5)


class DeviceHandoffEndpointTests(CompanionServerTestCase):
    def test_first_claim_has_no_previous_device_to_notify(self):
        subscriber = self.server.event_bus.subscribe()
        status, body = _post(f"{self.base_url}/devices/telefono/claim", {"name": "Telefono di Davide"})

        self.assertEqual(status, 200)
        self.assertEqual(body["active_device"], "telefono")
        self.assertEqual(self.server.devices.active_device_id, "telefono")
        self.assertTrue(subscriber.empty(), "nessun handoff da notificare al primo claim")

    def test_second_claim_publishes_a_handoff_event(self):
        _post(f"{self.base_url}/devices/pc/claim", {})
        subscriber = self.server.event_bus.subscribe()

        _post(f"{self.base_url}/devices/telefono/claim", {})

        event = subscriber.get(timeout=2)
        self.assertEqual(event.type, EventType.DEVICE_HANDOFF)
        self.assertEqual(event.payload, {"from": "pc", "to": "telefono"})
        self.assertEqual(self.server.devices.active_device_id, "telefono")

    def test_release_frees_the_active_device(self):
        _post(f"{self.base_url}/devices/pc/claim", {})
        status, body = _post(f"{self.base_url}/devices/pc/release", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["released"])
        self.assertIsNone(self.server.devices.active_device_id)


class EventStreamTests(CompanionServerTestCase):
    def test_published_event_arrives_over_the_sse_stream(self):
        from core.hud_protocol import HudEvent

        received = []

        def _read_stream():
            with request.urlopen(f"{self.base_url}/events", timeout=5) as response:
                for _ in range(10):
                    line = response.readline().decode("utf-8").strip()
                    if line.startswith("data: "):
                        received.append(line[len("data: "):])
                        return

        reader = threading.Thread(target=_read_stream, daemon=True)
        reader.start()
        time.sleep(0.2)  # da' tempo al thread del server di sottoscriversi al bus prima di pubblicare
        self.server.event_bus.publish(HudEvent(type=EventType.THINKING, payload={"detail": "prova"}))
        reader.join(timeout=5)

        self.assertEqual(len(received), 1)
        payload = json.loads(received[0])
        self.assertEqual(payload["type"], "THINKING")
        self.assertEqual(payload["payload"], {"detail": "prova"})


if __name__ == "__main__":
    unittest.main()
