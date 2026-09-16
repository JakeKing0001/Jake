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
chi ha gia' un uso locale/fidato del server non vede alcun cambiamento.

F1.4.6/F1.8.1 (fase 6/10 del piano multi-device - decisione di prodotto esplicita dell'utente,
vedi ROADMAP_EXECUTION.md sezione F1.4): buco reale nel design SOPRA, non solo teorico -
`device_id`/`session_id` erano SEMPRE letti dal BODY della richiesta (`body.get("device_id")`),
mai verificati contro nulla. Il singolo `companion_token` globale autorizza la RICHIESTA, non
dice CHI la sta facendo: qualunque client col token giusto poteva dichiararsi un `device_id`
qualsiasi (incluso quello di un ALTRO dispositivo gia' accoppiato) e cosi' vedere/confermare la
sua azione in sospeso (`ConversationStateManager._pending_actions`, gia' tenuta per-canale da
F1.8.1, ma "canale" = `current_device_id()` AUTO-DICHIARATO, non autenticato). `credential_store`
(opzionale, `core/device_credential_store.py`, fase 2) chiude il buco quando presente: un token
Bearer che verifica per-dispositivo produce un `device_id` AUTENTICATO che l'handler usa al posto
di quello nel body - un client non puo' piu' impersonare un dispositivo diverso dal proprio
semplicemente scrivendolo nella richiesta. Il vecchio `token` globale resta supportato
(retrocompatibilita' per chi non ha ancora fatto il pairing di alcun dispositivo): in quel caso
il comportamento e' IDENTICO a prima (device_id dal body, non autenticato) - un limite noto e
dichiarato del percorso legacy, non una regressione introdotta qui. Nessun fallback automatico
nella direzione opposta: un token per-dispositivo REVOCATO/SCADUTO non ripiega mai sul token
globale, anche se quello e' ancora configurato (F1.4.6, "non deve esistere fallback automatico a
un token globale")."""
import hmac
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from core.device_credential_store import DeviceCredentialStore
from core.device_registry import DeviceRegistry
from core.event_bus import EventBus
from core.hud_protocol import EventType, HudEvent
from core.request_context import (
    reset_current_device_id, reset_current_session_id, set_current_device_id, set_current_session_id,
)
from core.version import PROTOCOL_VERSION, VERSION

DEFAULT_HOST = "127.0.0.1"
SSE_KEEPALIVE_SECONDS = 15


class CompanionServer:
    """command_handler(text) -> str e' tipicamente JakeCore.answer: iniettabile cosi' i test non
    devono costruire un intero JakeCore per verificare il server (vedi tests/test_companion_
    server.py). port=0 (default) lascia scegliere una porta libera al sistema operativo.

    token (F1, opt-in): se impostato, ogni richiesta deve presentare "Authorization: Bearer
    <token>", altrimenti riceve 401 - vedi il docstring del modulo. credential_store (F1.4.6,
    fase 6, opt-in): se presente, un Bearer token che verifica per-dispositivo autentica la
    richiesta E produce un device_id fidato, invece di quello auto-dichiarato nel body."""

    def __init__(
        self, event_bus: EventBus | None = None, command_handler=None, host: str = DEFAULT_HOST, port: int = 0,
        token: str | None = None, credential_store: DeviceCredentialStore | None = None,
    ):
        self.event_bus = event_bus or EventBus()
        self.command_handler = command_handler or (lambda text: "")
        self.devices = DeviceRegistry()
        self.host = host
        self.port = port
        self.token = token
        self.credential_store = credential_store
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
        self._thread: threading.Thread | None = None
        super().__init__(address, handler_cls)

    def start_serving(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        super().shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2)


class _Handler(BaseHTTPRequestHandler):
    # F1.4.6 (fase 6): default di classe, non solo impostato in do_GET/do_POST - una rete di
    # sicurezza se un metodo lo leggesse prima che quelli girino (oggi non succede: BaseHTTPRequest
    # Handler chiama do_GET/do_POST dal proprio __init__), mai un AttributeError inatteso.
    _authenticated_device_id: str | None = None

    def log_message(self, format, *args):  # silenzia il log di default di http.server
        pass

    @property
    def companion(self) -> CompanionServer:
        return cast(_Server, self.server).companion

    # ---- autenticazione (F1, opt-in - vedi il docstring del modulo) ----------------------

    def _authenticate(self) -> tuple[bool, str | None]:
        """(autorizzata, device_id AUTENTICATO o None). device_id e' popolato solo quando
        credential_store verifica per davvero il token presentato - MAI dedotto dal body, che
        resta un campo auto-dichiarato (vedi _handle_command/_handle_claim/_handle_release, che
        lo usano SOLO quando questo e' None, il percorso legacy). Ordine dei controlli: prima il
        token per-dispositivo (piu' forte, produce un'identita'), poi il token globale legacy
        (autorizza ma non identifica), infine "nessun token configurato" (comportamento
        invariato per chi non ne ha impostato nessuno dei due). hmac.compare_digest per il
        confronto col token globale: un confronto normale su stringhe non e' a tempo costante, e
        anche su una rete locale non c'e' motivo di regalare un canale laterale temporale a chi
        indovina un token un carattere alla volta (device_credential_store.verify_token() applica
        gia' da sola lo stesso principio ai token per-dispositivo).

        Buco reale trovato E riprodotto (non solo temuto) scrivendo il test di questa fase:
        `credential_store` presente ma un token per-dispositivo che non verifica (sbagliato,
        revocato, mai emesso) NON deve mai ricadere silenziosamente su "nessun token configurato
        = aperto a chiunque" - solo perche' `self.companion.token` (il token GLOBALE legacy,
        un campo indipendente) e' None. Un `credential_store` configurato significa che
        l'autenticazione per-dispositivo e' IN USO: "aperto a chiunque" resta valido SOLO se
        nemmeno un `credential_store` e' stato passato al server."""
        header = self.headers.get("Authorization", "")
        presented = header[len("Bearer "):] if header.startswith("Bearer ") else None
        credential_store = self.companion.credential_store
        if credential_store is not None and presented:
            device_id = credential_store.verify_token(presented)
            if device_id is not None:
                return True, device_id
        token = self.companion.token
        if not token:
            return credential_store is None, None
        return hmac.compare_digest(header, f"Bearer {token}"), None

    # ---- routing ------------------------------------------------------------------------

    def do_GET(self):
        authorized, self._authenticated_device_id = self._authenticate()
        if not authorized:
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
        return None

    def do_POST(self):
        authorized, self._authenticated_device_id = self._authenticate()
        if not authorized:
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
        return None

    def _device_id_mismatch(self, url_device_id: str) -> bool:
        """Vero se questa richiesta e' autenticata per-dispositivo (F1.4.6, fase 6) E il
        device_id nell'URL non e' quello del token presentato - un dispositivo autenticato come
        se stesso non deve poter agire (claim/release) a nome di un ALTRO device_id solo perche'
        lo scrive nel path. Sempre falso quando non c'e' un'identita' autenticata (percorso
        legacy/nessun token): comportamento invariato per chi non ha ancora fatto il pairing."""
        return self._authenticated_device_id is not None and self._authenticated_device_id != url_device_id

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
        # F1.2.3/F1.8.1 (fondamenta): device_id opzionale nel body - se il client lo manda (lo
        # stesso id gia' usato per /claim), il resto della catena di chiamate su QUESTO thread
        # (JakeCore.answer -> ... -> ActionLedger) lo vede tramite core/request_context.py senza
        # che questo metodo debba passarlo esplicitamente. reset SEMPRE nel finally: anche se
        # ThreadingHTTPServer non riusa i thread tra richieste (ognuna ne crea uno nuovo, che
        # parte gia' dal default), resettare resta la scelta corretta a prescindere dai dettagli
        # di implementazione di chi gestisce le richieste.
        # F1.4.6 (fase 6): quando la richiesta e' autenticata per-dispositivo (token verificato
        # da credential_store, vedi _authenticate()), il device_id AUTENTICATO vince SEMPRE su
        # quello nel body - un client non puo' piu' impersonare un altro dispositivo scrivendone
        # semplicemente l'id nella richiesta. Il body resta l'unica fonte solo sul percorso
        # legacy (nessun token per-dispositivo verificato), comportamento invariato per chi non
        # ha ancora fatto il pairing di alcun dispositivo.
        device_id = self._authenticated_device_id or (body.get("device_id") or "").strip() or None
        device_token = set_current_device_id(device_id)
        # F1.2.3 (capability per SESSIONE): session_id opzionale nel body - lo stesso ricevuto da
        # /claim in risposta. Stesso schema/stesse garanzie di device_id sopra.
        session_id = (body.get("session_id") or "").strip() or None
        session_token = set_current_session_id(session_id)
        try:
            response = self.companion.command_handler(text)
        finally:
            reset_current_device_id(device_token)
            reset_current_session_id(session_token)
        self._json_response(200, {"response": response})
        return None

    def _handle_claim(self, device_id: str):
        body = self._read_json_body()
        # F1.4.6 (fase 6): un dispositivo autenticato con un token per-dispositivo puo'
        # reclamare SOLO se stesso - non gli id di altri dispositivi gia' accoppiati, anche se
        # possiede comunque un token valido (il proprio). Nessun controllo sul percorso legacy
        # (nessuna identita' autenticata da confrontare), comportamento invariato.
        if self._device_id_mismatch(device_id):
            return self._json_response(403, {"error": "device_id_mismatch"})
        name = body.get("name", "")
        # F1.2.3 (capability per SESSIONE): session_id e' NUOVO a ogni claim(), anche per lo
        # stesso device_id di prima - il client lo deve rimandare in /command (campo opzionale
        # "session_id", stesso schema gia' usato per "device_id") perche' il resto della catena
        # di chiamate su QUEL thread lo veda tramite core/request_context.py.
        previous, session_id = self.companion.devices.claim(device_id, name)
        if previous:
            self.companion.event_bus.publish(HudEvent(EventType.DEVICE_HANDOFF, {"from": previous, "to": device_id}))
        self._json_response(200, {"active_device": device_id, "session_id": session_id})
        return None

    def _handle_release(self, device_id: str):
        # _read_json_body() scarta il risultato (release non ha ancora parametri), ma va
        # comunque chiamato: se il client manda un body (anche vuoto, "{}") e il gestore non lo
        # legge, quei byte restano non letti nel buffer TCP quando la connessione si chiude. Su
        # Windows questo fa rispondere con un RST invece di una FIN pulita, e il client vede un
        # ConnectionAbortedError [WinError 10053] intermittente (dipende dal timing con cui i
        # byte del body arrivano rispetto alla chiusura) - il flake descritto nella roadmap F0,
        # riprodotto qui in ~10% delle richieste su 400+ esecuzioni finche' non si legge il body.
        self._read_json_body()
        # F1.4.6 (fase 6): stesso principio di _handle_claim - un dispositivo autenticato non
        # deve poter rilasciare un device_id che non e' il proprio.
        if self._device_id_mismatch(device_id):
            return self._json_response(403, {"error": "device_id_mismatch"})
        released = self.companion.devices.release(device_id)
        self._json_response(200, {"released": released})
        return None

    def _stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        # F4.1.3 ("resume dall'ultimo sequence id"): un client che riconnette manda l'header SSE
        # standard Last-Event-ID con l'ultimo sequence_id visto (id: <n> prima di ogni riga data:
        # sotto, lo stesso formato che i browser leggono da soli per un EventSource - qui letto a
        # mano perche' JakeClient.cpp parsa SSE manualmente, vedi il suo README). Un header
        # assente o non un intero valido e' trattato come "client nuovo, nessun replay" - stesso
        # comportamento di sempre, nessuna rottura per un client che non implementa ancora questo
        # pezzo (F4.1.3 lato C++, non ancora affrontato).
        last_event_id_header = self.headers.get("Last-Event-ID")
        since_sequence_id = 0
        if last_event_id_header is not None:
            try:
                since_sequence_id = int(last_event_id_header)
            except ValueError:
                since_sequence_id = 0
        subscriber, replayed, gap = self.companion.event_bus.subscribe_with_replay(since_sequence_id)
        try:
            if gap:
                # Commento SSE (righe che iniziano con ":", ignorate da qualunque parser SSE
                # standard incluso quello di JakeClient.cpp, vedi core/hud_protocol.py) - il
                # buffer di replay non copriva l'intera finestra richiesta, uno o piu' eventi sono
                # persi per sempre: dichiarato onestamente sul filo, non nascosto silenziosamente.
                self.wfile.write(b": jake-replay-gap - some events were permanently lost\n\n")
            for event in replayed:
                self.wfile.write(f"id: {event.sequence_id}\ndata: {event.to_json()}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    event = subscriber.get(timeout=SSE_KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"id: {event.sequence_id}\ndata: {event.to_json()}\n\n".encode("utf-8"))
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
