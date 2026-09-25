"""Test unitari per il server companion (v5.8/5.9/4.9.1, core/companion_server.py). A differenza
di NestClient/OllamaClient (dove Jake e' il CLIENTE e si mocka il lato remoto), qui Jake e' il
SERVER: i test avviano un'istanza vera su una porta effimera (127.0.0.1, port=0) e fanno vere
richieste HTTP con urllib, senza bisogno di un telefono o un secondo dispositivo reale."""
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib import error, request

from core.companion_server import CompanionServer
from core.device_credential_store import DeviceCredentialStore
from core.hud_protocol import EventType
from core.request_context import current_device_id, current_session_id
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

    def test_session_id_in_the_body_is_visible_to_the_handler_via_request_context(self):
        """F1.2.3 (capability per SESSIONE): stesso identico principio di device_id sopra, ma per
        l'id di una CONNESSIONE companion (quello restituito da /claim, vedi
        DeviceHandoffEndpointTests sotto)."""
        observed = []
        server = CompanionServer(command_handler=lambda text: observed.append(current_session_id()) or "ok")
        server.start()
        self.addCleanup(server.stop)

        status, body = _post(f"http://127.0.0.1:{server.port}/command", {"text": "ciao", "session_id": "sess-1"})

        self.assertEqual(status, 200)
        self.assertEqual(observed, ["sess-1"])

    def test_missing_session_id_leaves_the_context_at_its_default(self):
        observed = []
        server = CompanionServer(command_handler=lambda text: observed.append(current_session_id()) or "ok")
        server.start()
        self.addCleanup(server.stop)

        _post(f"http://127.0.0.1:{server.port}/command", {"text": "ciao"})

        self.assertEqual(observed, [None])

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

    def test_claim_returns_a_session_id_the_client_can_reuse_in_command(self):
        """F1.2.3 (capability per SESSIONE): il client deve poter leggere il session_id dalla
        risposta di /claim per poi rimandarlo in /command (vedi CommandEndpointTests sopra)."""
        status, body = _post(f"{self.base_url}/devices/telefono/claim", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["session_id"])

    def test_claiming_the_same_device_twice_returns_two_different_session_ids(self):
        _, first_body = _post(f"{self.base_url}/devices/telefono/claim", {})
        _, second_body = _post(f"{self.base_url}/devices/telefono/claim", {})
        self.assertNotEqual(first_body["session_id"], second_body["session_id"])

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

    def test_events_carry_an_id_line_matching_their_sequence_id(self):
        """F4.1.3: formato SSE standard `id: <n>` prima di ogni `data:` - lo stesso che un
        browser leggerebbe da solo per un EventSource, qui verificato a mano perche' JakeClient.
        cpp parsa SSE manualmente (vedi hud/native/README.md)."""
        from core.hud_protocol import HudEvent

        lines: list = []

        def _read_stream():
            with request.urlopen(f"{self.base_url}/events", timeout=5) as response:
                for _ in range(10):
                    line = response.readline().decode("utf-8").strip()
                    lines.append(line)
                    if line.startswith("data: "):
                        return

        reader = threading.Thread(target=_read_stream, daemon=True)
        reader.start()
        time.sleep(0.2)
        self.server.event_bus.publish(HudEvent(type=EventType.IDLE))
        reader.join(timeout=5)

        id_lines = [line for line in lines if line.startswith("id: ")]
        self.assertEqual(len(id_lines), 1)
        self.assertEqual(id_lines[0], "id: 1", "il primo evento pubblicato su un bus nuovo ha sequence_id 1")

    def test_reconnecting_with_last_event_id_replays_exactly_what_was_missed(self):
        """F4.1.3 ("resume dall'ultimo sequence id"): due eventi pubblicati PRIMA che il client
        SSE si connetta (simula una disconnessione breve durante cui il client ha perso eventi)
        - riconnettendo con Last-Event-ID: 1 il client deve ricevere SOLO il secondo (mai il
        primo, che aveva gia' visto; mai perderlo in silenzio)."""
        from core.hud_protocol import HudEvent

        self.server.event_bus.publish(HudEvent(type=EventType.USER_MESSAGE, payload={"text": "gia' visto"}))
        self.server.event_bus.publish(HudEvent(type=EventType.USER_MESSAGE, payload={"text": "perso durante il gap"}))

        received: list = []

        def _read_stream():
            req = request.Request(f"{self.base_url}/events", headers={"Last-Event-ID": "1"})
            with request.urlopen(req, timeout=5) as response:
                for _ in range(10):
                    line = response.readline().decode("utf-8").strip()
                    if line.startswith("data: "):
                        received.append(json.loads(line[len("data: "):]))
                        return

        reader = threading.Thread(target=_read_stream, daemon=True)
        reader.start()
        reader.join(timeout=5)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["payload"], {"text": "perso durante il gap"})
        self.assertEqual(received[0]["sequence_id"], 2)

    def test_reconnect_then_live_events_continue_seamlessly_after_the_replay(self):
        from core.hud_protocol import HudEvent

        self.server.event_bus.publish(HudEvent(type=EventType.USER_MESSAGE, payload={"text": "vecchio"}))
        received: list = []

        def _read_stream():
            req = request.Request(f"{self.base_url}/events", headers={"Last-Event-ID": "1"})
            try:
                with request.urlopen(req, timeout=5) as response:
                    for _ in range(10):
                        line = response.readline().decode("utf-8").strip()
                        if line.startswith("data: "):
                            received.append(json.loads(line[len("data: "):]))
                            if len(received) == 2:
                                return
            except TimeoutError:
                return  # lo stream resta aperto: le asserzioni sotto dicono se e' arrivato tutto

        reader = threading.Thread(target=_read_stream, daemon=True)
        reader.start()
        time.sleep(0.2)  # da' tempo alla richiesta di connettersi e ricevere il replay prima di pubblicare dal vivo
        self.server.event_bus.publish(HudEvent(type=EventType.USER_MESSAGE, payload={"text": "nuovo"}))
        reader.join(timeout=5)

        self.assertEqual([event["payload"]["text"] for event in received], ["nuovo"])

    def test_a_missing_or_invalid_last_event_id_behaves_like_a_fresh_connection(self):
        """Un client che non implementa ancora questo pezzo (o manda un header corrotto) non deve
        rompersi: nessun replay, comportamento identico a prima di F4.1.3."""
        from core.hud_protocol import HudEvent

        for header_value in (None, "non-un-numero", ""):
            with self.subTest(header=header_value):
                received: list = []

                def _read_stream(header_value=header_value, received=received):
                    headers = {"Last-Event-ID": header_value} if header_value is not None else {}
                    req = request.Request(f"{self.base_url}/events", headers=headers)
                    with request.urlopen(req, timeout=5) as response:
                        for _ in range(10):
                            line = response.readline().decode("utf-8").strip()
                            if line.startswith("data: "):
                                received.append(line)
                                return

                reader = threading.Thread(target=_read_stream, daemon=True)
                reader.start()
                time.sleep(0.2)
                self.server.event_bus.publish(HudEvent(type=EventType.IDLE))
                reader.join(timeout=5)

                self.assertEqual(len(received), 1, f"header={header_value!r}")


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


class PerDeviceTokenAuthenticationTests(unittest.TestCase):
    """F1.4.6/F1.8.1 (fase 6/10 del piano multi-device): buco reale nel design precedente - il
    singolo companion_token globale autorizzava la RICHIESTA, ma device_id era sempre letto dal
    BODY, mai verificato. Un client col token giusto poteva dichiararsi un device_id qualsiasi,
    incluso quello di un altro dispositivo, e cosi' vedere/confermare la sua azione in sospeso.
    Un vero DeviceCredentialStore (SQLite temporaneo, DPAPI reale) - non un doppio."""

    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_companion_server_device_auth_test_"))
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        self.store = DeviceCredentialStore(db_path=tmp_dir / "devices.db")
        self.addCleanup(self.store.close)
        self.credential_a = self.store.issue_credential("device-a")
        self.credential_b = self.store.issue_credential("device-b")

    def _server(self, command_handler=None, token: str | None = None) -> CompanionServer:
        server = CompanionServer(
            command_handler=command_handler or (lambda text: "ok"),
            credential_store=self.store, token=token,
        )
        server.start()
        self.addCleanup(server.stop)
        return server

    def _auth_header(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def test_a_valid_per_device_token_is_authorized(self):
        server = self._server()
        status, _ = _post(
            f"http://127.0.0.1:{server.port}/command", {"text": "ciao"},
            headers=self._auth_header(self.credential_a.token),
        )
        self.assertEqual(status, 200)

    def test_an_unknown_token_is_rejected(self):
        server = self._server()
        status, _ = _post(
            f"http://127.0.0.1:{server.port}/command", {"text": "ciao"},
            headers=self._auth_header("token-mai-emesso"),
        )
        self.assertEqual(status, 401)

    def test_a_revoked_per_device_token_is_rejected(self):
        self.store.revoke("device-a")
        server = self._server()
        status, _ = _post(
            f"http://127.0.0.1:{server.port}/command", {"text": "ciao"},
            headers=self._auth_header(self.credential_a.token),
        )
        self.assertEqual(status, 401)

    def test_the_authenticated_device_id_reaches_the_handler_not_the_bodys(self):
        """Il caso comune: il client onesto non manda affatto device_id nel body quando ha gia'
        un token per-dispositivo - l'identita' arriva comunque al gestore."""
        observed = []
        server = self._server(command_handler=lambda text: observed.append(current_device_id()) or "ok")

        _post(
            f"http://127.0.0.1:{server.port}/command", {"text": "ciao"},
            headers=self._auth_header(self.credential_a.token),
        )

        self.assertEqual(observed, ["device-a"])

    def test_a_device_cannot_impersonate_another_device_via_the_body(self):
        """Il buco reale che questa fase chiude: prima della correzione, un device_id nel body
        avrebbe vinto sempre - qui il token e' di device-a, il body dichiara device-b, deve
        vincere l'identita' AUTENTICATA."""
        observed = []
        server = self._server(command_handler=lambda text: observed.append(current_device_id()) or "ok")

        _post(
            f"http://127.0.0.1:{server.port}/command", {"text": "ciao", "device_id": "device-b"},
            headers=self._auth_header(self.credential_a.token),
        )

        self.assertEqual(observed, ["device-a"], "il device_id nel body non deve mai vincere su quello autenticato")

    def test_a_device_can_claim_itself(self):
        server = self._server()
        status, body = _post(
            f"http://127.0.0.1:{server.port}/devices/device-a/claim", {"name": "Il mio telefono"},
            headers=self._auth_header(self.credential_a.token),
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["active_device"], "device-a")

    def test_a_device_cannot_claim_a_different_device_id(self):
        """F1.4.6/F1.8.1 ('un altro dispositivo non deve poter confermare per errore una pending
        action non sua'): reclamare un device_id diverso dal proprio e' esattamente il primo
        passo per finire poi a leggere/confermare le azioni in sospeso di quell'altro
        dispositivo (ConversationStateManager e' tenuto per current_device_id())."""
        server = self._server()
        status, body = _post(
            f"http://127.0.0.1:{server.port}/devices/device-b/claim", {"name": "Furto di identita'"},
            headers=self._auth_header(self.credential_a.token),
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "device_id_mismatch")
        self.assertIsNone(server.devices.active_device_id, "il tentativo non deve aver reclamato nulla")

    def test_a_device_cannot_release_a_different_device_id(self):
        server = self._server()
        _post(
            f"http://127.0.0.1:{server.port}/devices/device-b/claim", {},
            headers=self._auth_header(self.credential_b.token),
        )

        status, body = _post(
            f"http://127.0.0.1:{server.port}/devices/device-b/release", {},
            headers=self._auth_header(self.credential_a.token),
        )

        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "device_id_mismatch")
        self.assertEqual(server.devices.active_device_id, "device-b", "device-b deve restare rivendicato")

    def test_status_endpoint_accepts_a_valid_per_device_token(self):
        server = self._server()
        status, body = _get(f"http://127.0.0.1:{server.port}/status", headers=self._auth_header(self.credential_a.token))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_the_legacy_global_token_still_works_alongside_a_credential_store(self):
        """Retrocompatibilita' deliberata (vedi il docstring del modulo): un dispositivo che non
        ha ancora fatto il pairing puo' continuare a usare il token globale - percorso legacy,
        device_id resta quello (eventuale) del body, comportamento invariato."""
        observed = []
        server = self._server(
            command_handler=lambda text: observed.append(current_device_id()) or "ok", token="token-globale",
        )

        status, _ = _post(
            f"http://127.0.0.1:{server.port}/command", {"text": "ciao", "device_id": "non-verificato"},
            headers=self._auth_header("token-globale"),
        )

        self.assertEqual(status, 200)
        self.assertEqual(observed, ["non-verificato"], "sul percorso legacy il body resta l'unica fonte, come prima")


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
