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
from core.hud_protocol import EventType
from core.request_context import current_device_id
from core.version import PROTOCOL_VERSION, VERSION


def _get(url: str, timeout: float = 5, headers: dict = None) -> tuple[int, dict]:
    req = request.Request(url, headers=headers or {})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(url: str, payload: dict, timeout: float = 5, headers: dict = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    req = request.Request(url, data=body, headers=all_headers, method="POST")
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
        self.assertEqual(body["version"], VERSION)
        self.assertEqual(body["protocol_version"], PROTOCOL_VERSION)
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

    def test_extra_fields_cannot_smuggle_authorization_signals_to_the_handler(self):
        """F1.2.8 (bypass test per il percorso 4 di docs/action-execution-paths.md): il body
        del companion arriva a command_handler(text) come stringa nuda - non esiste, a
        differenza di PlanExecutor (vedi core/policy_engine.py::strip_authorization_signals),
        un percorso strutturato "intent"/"parameters" per questo endpoint. Un client companion
        non puo' quindi mai far arrivare "confirmed"/"authenticated" gia' impostati a
        JakeCore._resolve_and_execute: solo il testo, sempre ri-analizzato dall'NLU."""
        status, body = _post(f"{self.base_url}/command", {
            "text": "che ore sono", "intent": "DELETE_PATH",
            "parameters": {"path": "C:\\qualsiasi", "confirmed": True}, "confirmed": True,
            "authenticated": True,
        })
        self.assertEqual(status, 200)
        # command_handler ha ricevuto SOLO la stringa "che ore sono" - gli altri campi del body
        # non sono mai arrivati come argomento: se lo fossero, sarebbero un dict/oggetto, non la
        # str che _fake_answer registra qui.
        self.assertEqual(self.command_calls, ["che ore sono"])

    def test_device_id_in_the_body_is_visible_to_the_handler_via_request_context(self):
        """F1.2.3/F1.8.1 (fondamenta): il body puo' includere lo stesso device_id gia' usato per
        /claim - il command_handler (JakeCore.answer nel caso reale) lo vede tramite
        core/request_context.py senza che arrivi come argomento esplicito (vedi il test sopra:
        command_handler continua a ricevere SOLO il testo)."""
        observed = []
        server = CompanionServer(command_handler=lambda text: observed.append(current_device_id()) or "ok")
        server.start()
        self.addCleanup(server.stop)

        status, body = _post(f"http://127.0.0.1:{server.port}/command", {"text": "ciao", "device_id": "phone1"})

        self.assertEqual(status, 200)
        self.assertEqual(observed, ["phone1"])

    def test_missing_device_id_leaves_the_context_at_its_default(self):
        observed = []
        server = CompanionServer(command_handler=lambda text: observed.append(current_device_id()) or "ok")
        server.start()
        self.addCleanup(server.stop)

        _post(f"http://127.0.0.1:{server.port}/command", {"text": "ciao"})

        self.assertEqual(observed, [None])

    def test_device_id_does_not_leak_to_a_request_from_a_different_device(self):
        """Due richieste companion concorrenti da dispositivi diversi non devono mai vedersi a
        vicenda il device_id (ThreadingHTTPServer: ogni richiesta gira sul proprio thread) -
        stessa proprieta' di sicurezza gia' verificata in isolamento in
        tests/test_request_context.py, qui end-to-end attraverso l'intero server HTTP."""
        release_second_request = threading.Event()
        observed = {}

        def _handler(text):
            device_id = current_device_id()
            if device_id == "phone1":
                release_second_request.wait(timeout=5)
            observed[device_id] = text
            return "ok"

        server = CompanionServer(command_handler=_handler)
        server.start()
        self.addCleanup(server.stop)
        url = f"http://127.0.0.1:{server.port}/command"

        first = threading.Thread(target=_post, args=(url, {"text": "dal telefono", "device_id": "phone1"}))
        first.start()
        time.sleep(0.05)  # lascia partire la prima richiesta e bloccarsi in attesa dell'evento
        _post(url, {"text": "dal tablet", "device_id": "tablet1"})
        release_second_request.set()
        first.join(timeout=5)

        self.assertEqual(observed, {"phone1": "dal telefono", "tablet1": "dal tablet"})

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


class TokenAuthenticationTests(unittest.TestCase):
    """F1 (Identity & Authentication, "capability token... per dispositivo" - vedi ROADMAP.md):
    fino a questa correzione NESSUN endpoint richiedeva autenticazione - qualunque processo
    capace di raggiungere la porta poteva mandare comandi a Jake con gli stessi privilegi
    dell'utente. token e' opt-in: senza (vedi le classi sopra, CompanionServerTestCase non lo
    imposta mai) il comportamento resta invariato."""

    def setUp(self):
        self.command_calls = []
        self.server = CompanionServer(command_handler=self._fake_answer, token="segreto-di-test")
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base_url = f"http://127.0.0.1:{self.server.port}"

    def _fake_answer(self, text: str) -> str:
        self.command_calls.append(text)
        return f"risposta a: {text}"

    def _auth_header(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def test_command_without_any_authorization_header_is_rejected(self):
        status, body = _post(f"{self.base_url}/command", {"text": "che ore sono"})

        self.assertEqual(status, 401)
        self.assertEqual(self.command_calls, [], "il comando non deve mai raggiungere il gestore senza autorizzazione")

    def test_command_with_the_wrong_token_is_rejected(self):
        status, body = _post(
            f"{self.base_url}/command", {"text": "che ore sono"}, headers=self._auth_header("token-sbagliato"),
        )

        self.assertEqual(status, 401)
        self.assertEqual(self.command_calls, [])

    def test_command_with_the_correct_token_succeeds(self):
        status, body = _post(
            f"{self.base_url}/command", {"text": "che ore sono"}, headers=self._auth_header("segreto-di-test"),
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.command_calls, ["che ore sono"])

    def test_status_endpoint_also_requires_the_token(self):
        unauthorized_status, _ = _get(f"{self.base_url}/status")
        authorized_status, body = _get(f"{self.base_url}/status", headers=self._auth_header("segreto-di-test"))

        self.assertEqual(unauthorized_status, 401)
        self.assertEqual(authorized_status, 200)
        self.assertTrue(body["ok"])

    def test_device_claim_without_the_token_is_rejected_and_does_not_claim_anything(self):
        status, _ = _post(f"{self.base_url}/devices/telefono/claim", {"name": "Telefono"})

        self.assertEqual(status, 401)
        self.assertIsNone(self.server.devices.active_device_id, "il dispositivo non deve risultare rivendicato")

    def test_unknown_path_is_401_before_404_when_unauthorized(self):
        """L'autenticazione si controlla PRIMA del routing: un percorso inesistente non deve
        rivelare la propria non-esistenza a chi non e' nemmeno autorizzato a chiedere."""
        status, _ = _get(f"{self.base_url}/non-esiste")

        self.assertEqual(status, 401)


class NoTokenConfiguredIsBackwardCompatibleTests(CompanionServerTestCase):
    """CompanionServerTestCase (in cima al file) costruisce il server senza mai passare
    token=...: verifica esplicitamente che il default resti None, non una stringa vuota o
    un'altra sorpresa silenziosa."""

    def test_token_defaults_to_none(self):
        self.assertIsNone(self.server.token)

    def test_requests_succeed_without_any_authorization_header(self):
        status, _ = _get(f"{self.base_url}/status")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
