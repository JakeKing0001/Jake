"""Server locale companion (v5.8 Mobile Companion, v5.9 Ambient Computing, v4.9.1 HUD IPC
transport): espone Jake in rete locale a QUALSIASI client esterno - un'app companion su
telefono, un futuro HUD nativo C++/Qt6/QML (vedi hud/native/), un altro dispositivo che vuole
prendere in carico la sessione - senza che quel client debba essere un processo Python nello
stesso interprete.

Solo libreria standard (http.server): stesso principio gia' seguito da core/ollama_client.py
("Nessuna dipendenza esterna"), niente websocket/flask/fastapi in piu' da installare. Lo
streaming eventi usa Server-Sent Events (una risposta HTTP che resta aperta e invia un blocco
"data: ...\\n\\n" per evento): supportato nativamente da EventSource nei browser/webview/Qt e
leggibile da qualunque client HTTP capace di leggere uno stream via chunked transfer encoding.

Disattivato per default: va avviato esplicitamente (CompanionServer.start()), non parte da solo
con Jake. Ascolta solo su 127.0.0.1 per default: esporlo alla rete locale (per un telefono sulla
stessa Wi-Fi) e' una scelta esplicita di chi lo avvia (host="0.0.0.0"), non il comportamento
predefinito.

F1 (Identity & Authentication, "capability token... per dispositivo" in ROADMAP.md): fino a
questa correzione NESSUN endpoint richiedeva alcuna autenticazione - qualunque processo capace
di raggiungere la porta (oggi solo altri processi sulla stessa macchina, dato che
companion_server_host non e' ancora esposto in config.json; domani, quando lo sara' per un
telefono sulla stessa Wi-Fi, chiunque su quella rete) poteva mandare comandi a Jake con gli
stessi privilegi dell'utente - inclusa la possibilita' di rivendicare la sessione attiva
(/devices/<id>/claim) senza autorizzazione. Il token (config.json: companion_token, cifrato a
riposo via DPAPI come admin_passphrase, vedi core/config.py SECRET_KEYS) e' opt-in: se non
configurato, il comportamento resta invariato (nessun controllo, come prima di questa fase) -
chi ha gia' un uso locale/fidato del server non vede alcun cambiamento."""
import hmac
import json
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.device_registry import DeviceRegistry
from core.event_bus import EventBus
from core.hud_protocol import EventType, HudEvent
from core.version import PROTOCOL_VERSION, VERSION

DEFAULT_HOST = "127.0.0.1"
SSE_KEEPALIVE_SECONDS = 15


class CompanionServer:
    """command_handler(text) -> str e' tipicamente JakeCore.answer: iniettabile cosi' i test non
    devono costruire un intero JakeCore per verificare il server (vedi tests/test_companion_
    server.py). port=0 (default) lascia scegliere una porta libera al sistema operativo.

    token (F1, opt-in): se impostato, ogni richiesta deve presentare "Authorization: Bearer
    <token>", altrimenti riceve 401 - vedi il docstring del modulo."""

    def __init__(
        self, event_bus: EventBus = None, command_handler=None, host: str = DEFAULT_HOST, port: int = 0,
        token: str = None,
    ):
        self.event_bus = event_bus or EventBus()
        self.command_handler = command_handler or (lambda text: "")
        self.devices = DeviceRegistry()
        self.host = host
        self.port = port
        self.token = token
        self._httpd: "_Server | None" = None

    @property
    def address(self) -> tuple:
        if self._httpd is not None:
            return self._httpd.server_address
        return (self.host, self.port)

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = _Server((self.host, self.port), _Handler, self)
        self.port = self._httpd.server_address[1]
        self._httpd.start_serving()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler_cls, companion: CompanionServer):
        self.companion = companion
        self._thread = None
        super().__init__(address, handler_cls)

    def start_serving(self) -> None:
        import threading

        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        super().shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # silenzia il log di default di http.server
        pass

    @property
    def companion(self) -> CompanionServer:
        return self.server.companion

    # ---- autenticazione (F1, opt-in - vedi il docstring del modulo) ----------------------

    def _is_authorized(self) -> bool:
        """Vero se non e' configurato nessun token (comportamento invariato) o se la richiesta
        presenta il token giusto in 'Authorization: Bearer <token>'. hmac.compare_digest invece
        di '==': un confronto normale su stringhe non e' a tempo costante, e anche su una rete
        locale non c'e' motivo di regalare un canale laterale temporale a chi indovina un
        token un carattere alla volta."""
        token = self.companion.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {token}")

    # ---- routing ------------------------------------------------------------------------

    def do_GET(self):
        if not self._is_authorized():
            return self._json_response(401, {"error": "unauthorized"})
        if self.path == "/status":
            return self._json_response(200, {
                "ok": True,
                "version": VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "active_device": self.companion.devices.active_device_id,
                "devices": self.companion.devices.list_devices(),
            })
        if self.path == "/events":
            return self._stream_events()
        self._json_response(404, {"error": "not_found"})

    def do_POST(self):
        if not self._is_authorized():
            # Drena comunque il body: byte non letti nel buffer di ricezione quando la
            # connessione si chiude fanno rispondere con un RST su Windows invece di una FIN
            # pulita (lo stesso flake intermittente [WinError 10053] gia' descritto e risolto
            # per _handle_release in F0 - vedi ROADMAP.md).
            self._read_json_body()
            return self._json_response(401, {"error": "unauthorized"})
        if self.path == "/command":
            return self._handle_command()
        if self.path.startswith("/devices/") and self.path.endswith("/claim"):
            device_id = self.path.split("/")[2]
            return self._handle_claim(device_id)
        if self.path.startswith("/devices/") and self.path.endswith("/release"):
            device_id = self.path.split("/")[2]
            return self._handle_release(device_id)
        self._json_response(404, {"error": "not_found"})

    # ---- endpoint -----------------------------------------------------------------------

    def _handle_command(self):
        # USER_MESSAGE/JAKE_MESSAGE non vengono pubblicati qui: quando command_handler e'
        # JakeCore.answer (il caso reale), li pubblica gia' lui sullo stesso event_bus - e per
        # OGNI scambio, non solo quelli arrivati via companion server (anche voce/CLI). Farlo
        # anche qui li pubblicherebbe due volte. Un command_handler indipendente da JakeCore, se
        # vuole quella visibilita', deve pubblicarla da se'.
        body = self._read_json_body()
        text = (body.get("text") or "").strip()
        if not text:
            return self._json_response(400, {"error": "missing_text"})
        response = self.companion.command_handler(text)
        self._json_response(200, {"response": response})

    def _handle_claim(self, device_id: str):
        body = self._read_json_body()
        name = body.get("name", "")
        previous = self.companion.devices.claim(device_id, name)
        if previous:
            self.companion.event_bus.publish(HudEvent(EventType.DEVICE_HANDOFF, {"from": previous, "to": device_id}))
        self._json_response(200, {"active_device": device_id})

    def _handle_release(self, device_id: str):
        # _read_json_body() scarta il risultato (release non ha ancora parametri), ma va
        # comunque chiamato: se il client manda un body (anche vuoto, "{}") e il gestore non lo
        # legge, quei byte restano non letti nel buffer TCP quando la connessione si chiude. Su
        # Windows questo fa rispondere con un RST invece di una FIN pulita, e il client vede un
        # ConnectionAbortedError [WinError 10053] intermittente (dipende dal timing con cui i
        # byte del body arrivano rispetto alla chiusura) - il flake descritto nella roadmap F0,
        # riprodotto qui in ~10% delle richieste su 400+ esecuzioni finche' non si legge il body.
        self._read_json_body()
        released = self.companion.devices.release(device_id)
        self._json_response(200, {"released": released})

    def _stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        subscriber = self.companion.event_bus.subscribe()
        try:
            while True:
                try:
                    event = subscriber.get(timeout=SSE_KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {event.to_json()}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            self.companion.event_bus.unsubscribe(subscriber)

    # ---- utilita' -----------------------------------------------------------------------

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _json_response(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
