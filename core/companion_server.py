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
un token globale").

F7.1 (protocollo companion sicuro): le difese che stanno PRIMA di un comando vivono in
core/companion_guard.py e questo server le applica in una pipeline unica (`_Handler._prepare`):
versione del protocollo -> blocco per tentativi falliti -> autenticazione -> rate limit -> capability
della classe di endpoint (read-only/command/approval/file/audio) -> anti-replay; poi limite e
validazione del corpo, audit di ogni comando remoto e handoff, e TLS (`tls_context`) obbligatorio
per ogni host non locale. Vedi il docstring di quel modulo per i limiti dichiarati."""
import hmac
import json
import queue
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from core.companion_guard import (
    MAX_NAME_CHARS, MIN_PROTOCOL_VERSION, CompanionGuard, EndpointClass, ValidationError, check_bind_policy,
    check_protocol, classify, new_request_id, text_fingerprint, validate_claim_body, validate_command_body,
    validate_identifier, validate_text,
)
from core.device_credential_store import DeviceCredentialStore
from core.device_identity import DeviceCredential
from core.device_registry import DeviceRegistry
from core.event_bus import EventBus
from core.hud_protocol import EventType, HudEvent
from core.logger import get_logger
from core.request_context import (
    reset_current_device_id, reset_current_session_id, set_current_device_id, set_current_session_id,
)
from core.version import PROTOCOL_VERSION, VERSION

DEFAULT_HOST = "127.0.0.1"
SSE_KEEPALIVE_SECONDS = 15
# Tempo massimo per leggere una richiesta (e per completare l'handshake TLS): chi apre la connessione e poi tace
# (slowloris) libera il thread invece di occuparlo per sempre. Non vale per l'attesa sullo stream SSE, che e' solo
# scrittura del server.
REQUEST_TIMEOUT_SECONDS = 30
# Oltre questa dimensione di corpo dichiarata non si prova nemmeno a svuotare il buffer prima di rifiutare.
_DRAIN_FACTOR = 4

_logger = get_logger("companion_server")


class CompanionServer:
    """command_handler(text) -> str e' tipicamente JakeCore.answer: iniettabile cosi' i test non
    devono costruire un intero JakeCore per verificare il server (vedi tests/test_companion_
    server.py). port=0 (default) lascia scegliere una porta libera al sistema operativo.

    token (F1, opt-in): se impostato, ogni richiesta deve presentare "Authorization: Bearer
    <token>", altrimenti riceve 401 - vedi il docstring del modulo. credential_store (F1.4.6,
    fase 6, opt-in): se presente, un Bearer token che verifica per-dispositivo autentica la
    richiesta E produce un device_id fidato, invece di quello auto-dichiarato nel body.

    F7.1: `guard` (`core/companion_guard.py`) raccoglie rate limit, anti-replay, limite del body,
    capability per classe di endpoint e audit, con default sicuri (nessuna configurazione = gia'
    protetto). `tls_context` (un `ssl.SSLContext` di server) cifra il canale; senza, `start()` rifiuta
    ogni host non locale (F7.1.4).

    F7.1.2/Companion Mobile MVP: `pairing_service` (opzionale) abilita `/pairing/start`/
    `/pairing/<id>` - senza, quei due endpoint rispondono 404 "pairing_not_configured", stesso
    principio opt-in di `credential_store`/`tls_context`. `conversation_state` (opzionale) abilita
    `/approvals/<task_id>` - senza, ogni approvazione risponde "nessuna decisione in sospeso" (fail
    closed, mai un tentativo di risolvere qualcosa alla cieca). `on_pairing_requested(challenge,
    requested_name, sync_public_key)` e' il collegamento al canale LOCALE (voce/CLI) dove l'utente
    da' l'approvazione esplicita - vedi `core/jake_core.py::_on_pairing_requested`."""

    def __init__(
        self, event_bus: EventBus | None = None, command_handler=None, host: str = DEFAULT_HOST, port: int = 0,
        token: str | None = None, credential_store: DeviceCredentialStore | None = None,
        tls_context: ssl.SSLContext | None = None, guard: CompanionGuard | None = None,
        pairing_service=None, conversation_state=None, on_pairing_requested=None, tls_fingerprint: str | None = None,
    ):
        self.event_bus = event_bus or EventBus()
        self.command_handler = command_handler or (lambda text: "")
        self.devices = DeviceRegistry()
        self.host = host
        self.port = port
        self.token = token
        self.credential_store = credential_store
        self.tls_context = tls_context
        self.guard = guard or CompanionGuard()
        self.pairing_service = pairing_service
        self.conversation_state = conversation_state
        self.on_pairing_requested = on_pairing_requested
        # F7.2 (Companion Mobile MVP): SOLO un dato da mostrare durante il pairing (trust-on-first-use, vedi
        # core/companion_tls.py - questo modulo non calcola ne' verifica nulla, resta puro protocollo/routing) -
        # None quando tls_context e' None o non e' stato passato.
        self.tls_fingerprint = tls_fingerprint
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
        # F7.1.4: PRIMA di aprire la porta - un bind LAN senza TLS non deve esistere nemmeno per un istante.
        check_bind_policy(self.host, self.tls_context is not None)
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

    def get_request(self):
        sock, address = super().get_request()
        if self.companion.tls_context is not None:
            # L'handshake NON si fa qui (girerebbe sul thread che accetta: un client lento bloccherebbe tutti):
            # si fa alla prima lettura, nel thread della richiesta, dove vale REQUEST_TIMEOUT_SECONDS.
            sock = self.companion.tls_context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, address

    def handle_error(self, request, client_address):
        # Un handshake fallito, un client che chiude a meta' o un timeout non sono errori del server: senza questo
        # socketserver stamperebbe un traceback su stderr per ciascuno.
        _logger.debug("Richiesta companion da %s terminata con errore", client_address, exc_info=True)

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
    _request_id: str | None = None
    _cls: EndpointClass | None = None
    _path: str = ""
    _method: str = ""
    _identity: str = "ip:?"
    timeout = REQUEST_TIMEOUT_SECONDS

    def log_message(self, format, *args):  # silenzia il log di default di http.server
        pass

    @property
    def companion(self) -> CompanionServer:
        return cast(_Server, self.server).companion

    @property
    def _remote(self) -> str:
        return str(self.client_address[0])

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

    # ---- pipeline comune (F7.1.1, F7.1.5-F7.1.7) ---------------------------------------------

    def _prepare(self, method: str) -> bool:
        """Tutto cio' che una richiesta deve superare PRIMA di raggiungere un gestore, in un ordine che non regala
        informazioni a chi non e' autorizzato: versione del protocollo, blocco per troppi tentativi falliti,
        autenticazione, frequenza, capability della classe di endpoint, anti-replay. Ritorna False (con la risposta
        gia' inviata e la riga di audit gia' scritta) se la richiesta e' stata respinta."""
        guard = self.companion.guard
        self._request_id = new_request_id()
        self._method = method
        self._path = self.path.split("?", 1)[0]
        self._cls = classify(method, self._path)
        self._identity = f"ip:{self._remote}"

        compatible, supported = check_protocol(self.headers.get("X-Jake-Protocol"))
        if not compatible:
            return self._reject(426, "protocol_unsupported", extra=supported)
        if self._cls == EndpointClass.PAIRING:
            # F7.1.2: raggiungibile PER COSTRUZIONE senza credenziali (un dispositivo nuovo non ne
            # ha ancora una) - la sicurezza viene dalla conferma esplicita sul PC
            # (core/pairing_service.py), non da un token. Resta comunque protetto dal rate limit
            # (per indirizzo, l'unica identita' disponibile) e da tutto cio' che segue
            # (validazione del corpo, audit): salta SOLO blocco-per-tentativi/autenticazione/
            # capability/anti-replay, non l'intera pipeline.
            ok, wait = guard.rate_limiter.allow(self._identity, EndpointClass.PAIRING)
            if not ok:
                return self._reject(429, "rate_limited", retry_after=wait)
            self._authenticated_device_id = None
            return True
        wait = guard.auth_throttle.retry_after(self._identity)
        if wait > 0:
            return self._reject(429, "too_many_auth_failures", retry_after=wait)
        authorized, self._authenticated_device_id = self._authenticate()
        if not authorized:
            # Nessun reset dei fallimenti su un successo: su 127.0.0.1 un client legittimo che interroga di continuo
            # azzererebbe il conteggio di chi sta indovinando il token dallo stesso indirizzo.
            guard.auth_throttle.record_failure(self._identity)
            return self._reject(401, "unauthorized", event="auth_failed")
        if self._authenticated_device_id is not None:
            self._identity = f"dev:{self._authenticated_device_id}"
        ok, wait = guard.rate_limiter.allow(self._identity, self._cls or EndpointClass.READ_ONLY)
        if not ok:
            return self._reject(429, "rate_limited", retry_after=wait)
        if self._cls is not None and not guard.allowed(self._authenticated_device_id, self._cls):
            return self._reject(403, "capability_denied", extra={"required": self._cls.value})
        needs_replay = self._cls not in (None, EndpointClass.READ_ONLY)
        problem = guard.replay.check(
            self._identity, self.headers.get("X-Jake-Timestamp"), self.headers.get("X-Jake-Nonce"),
            required=needs_replay and guard.replay_required(self.companion.host),
        )
        if problem is not None:
            status = 409 if problem == "replayed_nonce" else 503 if problem == "replay_cache_full" else 400
            extra = {"server_time": time.time()} if problem == "stale_timestamp" else None
            return self._reject(status, problem, extra=extra)
        return True

    def _audit(self, event: str, **fields) -> bool:
        """Scrive una riga di audit. False se il registro non e' scrivibile: chi esegue un COMANDO deve trattarlo
        come un rifiuto - nessun comando remoto deve girare senza traccia (F7.1.6)."""
        audit = self.companion.guard.audit
        if audit is None:
            return True
        try:
            audit.record(
                event, request_id=self._request_id, method=self._method, endpoint=self._path,
                endpoint_class=self._cls.value if self._cls else None, device_id=self._authenticated_device_id,
                authenticated=self._authenticated_device_id is not None, remote=self._remote, **fields,
            )
            return True
        except OSError:
            _logger.error("Audit companion non scrivibile", exc_info=True)
            return False

    def _drain_body(self) -> None:
        """Svuota il corpo non letto (se ragionevole) prima di una risposta di rifiuto: byte rimasti nel buffer di
        ricezione quando la connessione si chiude fanno rispondere con un RST su Windows invece di una FIN pulita
        (il flake [WinError 10053] descritto per _handle_release in F0 - vedi ROADMAP.md)."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return
        if 0 < length <= self.companion.guard.max_body_bytes * _DRAIN_FACTOR:
            try:
                self.rfile.read(length)
            except OSError:
                pass

    def _reject(self, status: int, error: str, *, event: str = "rejected", retry_after: float | None = None,
                extra: dict | None = None) -> bool:
        if self._method == "POST":
            self._drain_body()
        self._audit(event, status=status, reason=error)
        payload = {"error": error, **(extra or {})}
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = str(max(1, int(retry_after + 0.999)))
            payload["retry_after"] = round(retry_after, 2)
        self._json_response(status, payload, headers)
        return False

    # ---- routing ------------------------------------------------------------------------------------------

    def do_GET(self):
        if not self._prepare("GET"):
            return None
        if self._path == "/status":
            return self._json_response(200, {
                "ok": True,
                "version": VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "min_protocol_version": MIN_PROTOCOL_VERSION,
                "encrypted": self.companion.tls_context is not None,
                "active_device": self.companion.devices.active_device_id,
                "devices": self.companion.devices.list_devices(),
            })
        if self._path == "/events":
            return self._stream_events()
        if self._path == "/devices":
            return self._handle_list_devices()
        if self._path.startswith("/pairing/"):
            challenge_id = self._path[len("/pairing/"):]
            try:
                validated = validate_identifier(challenge_id, "challenge_id", required=True)
            except ValidationError as exc:
                self._reject(400, exc.code)
                return None
            assert validated is not None
            return self._handle_pairing_poll(validated)
        self._json_response(404, {"error": "not_found"})
        return None

    def do_POST(self):
        if not self._prepare("POST"):
            return None
        if self._path == "/command":
            return self._handle_command()
        if self._path == "/pairing/start":
            return self._handle_pairing_start()
        parts = self._path.split("/")
        if len(parts) == 4 and parts[1] == "devices" and parts[3] in ("claim", "release", "revoke"):
            try:
                device_id = validate_identifier(parts[2], "device_id", required=True)
            except ValidationError as exc:
                self._reject(400, exc.code)
                return None
            assert device_id is not None
            if parts[3] == "claim":
                return self._handle_claim(device_id)
            if parts[3] == "release":
                return self._handle_release(device_id)
            return self._handle_revoke(device_id)
        if len(parts) == 3 and parts[1] == "approvals":
            try:
                task_id = validate_identifier(parts[2], "task_id", required=True)
            except ValidationError as exc:
                self._reject(400, exc.code)
                return None
            assert task_id is not None
            return self._handle_approval(task_id)
        self._drain_body()
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
        body = self._read_json_object()
        if body is None:
            return None
        try:
            fields = validate_command_body(body)
        except ValidationError as exc:
            return self._reject_body(400, exc.code)
        text = fields["text"]
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
        device_id = self._authenticated_device_id or fields["device_id"]
        # F7.1.6: la riga "ricevuto" si scrive PRIMA di eseguire. Se il registro non e' scrivibile il comando NON
        # gira: un comando remoto senza traccia e' peggio di un comando rifiutato.
        if not self._audit("command_received", claimed_device_id=fields["device_id"], **text_fingerprint(text)):
            return self._reject_body(503, "audit_unavailable")
        device_token = set_current_device_id(device_id)
        # F1.2.3 (capability per SESSIONE): session_id opzionale nel body - lo stesso ricevuto da
        # /claim in risposta. Stesso schema/stesse garanzie di device_id sopra.
        session_token = set_current_session_id(fields["session_id"])
        started = time.monotonic()
        try:
            response = self.companion.command_handler(text)
        except Exception as exc:
            self._audit("command_failed", error=type(exc).__name__, duration_ms=round((time.monotonic() - started) * 1000))
            _logger.exception("Errore nel gestore del comando companion")
            return self._json_response(500, {"error": "command_failed"})
        finally:
            reset_current_device_id(device_token)
            reset_current_session_id(session_token)
        self._audit("command_completed", status=200, duration_ms=round((time.monotonic() - started) * 1000))
        self._json_response(200, {"response": response})
        return None

    def _handle_claim(self, device_id: str):
        body = self._read_json_object()
        if body is None:
            return None
        # F1.4.6 (fase 6): un dispositivo autenticato con un token per-dispositivo puo'
        # reclamare SOLO se stesso - non gli id di altri dispositivi gia' accoppiati, anche se
        # possiede comunque un token valido (il proprio). Nessun controllo sul percorso legacy
        # (nessuna identita' autenticata da confrontare), comportamento invariato.
        if self._device_id_mismatch(device_id):
            return self._reject_body(403, "device_id_mismatch")
        try:
            name = validate_claim_body(body)["name"]
        except ValidationError as exc:
            return self._reject_body(400, exc.code)
        # F1.2.3 (capability per SESSIONE): session_id e' NUOVO a ogni claim(), anche per lo
        # stesso device_id di prima - il client lo deve rimandare in /command (campo opzionale
        # "session_id", stesso schema gia' usato per "device_id") perche' il resto della catena
        # di chiamate su QUEL thread lo veda tramite core/request_context.py.
        previous, session_id = self.companion.devices.claim(device_id, name)
        self._audit("handoff_claim", status=200, target_device=device_id, previous_device=previous)
        if previous:
            self.companion.event_bus.publish(HudEvent(EventType.DEVICE_HANDOFF, {"from": previous, "to": device_id}))
        self._json_response(200, {"active_device": device_id, "session_id": session_id})
        return None

    def _handle_release(self, device_id: str):
        # _read_json_object() scarta il risultato (release non ha ancora parametri), ma va
        # comunque chiamato: se il client manda un body (anche vuoto, "{}") e il gestore non lo
        # legge, quei byte restano non letti nel buffer TCP quando la connessione si chiude. Su
        # Windows questo fa rispondere con un RST invece di una FIN pulita, e il client vede un
        # ConnectionAbortedError [WinError 10053] intermittente (dipende dal timing con cui i
        # byte del body arrivano rispetto alla chiusura) - il flake descritto nella roadmap F0,
        # riprodotto qui in ~10% delle richieste su 400+ esecuzioni finche' non si legge il body.
        if self._read_json_object() is None:
            return None
        # F1.4.6 (fase 6): stesso principio di _handle_claim - un dispositivo autenticato non
        # deve poter rilasciare un device_id che non e' il proprio.
        if self._device_id_mismatch(device_id):
            return self._reject_body(403, "device_id_mismatch")
        released = self.companion.devices.release(device_id)
        self._audit("handoff_release", status=200, target_device=device_id, released=released)
        self._json_response(200, {"released": released})
        return None

    # ---- dispositivi accoppiati (F7.2.1, Companion Mobile MVP: "lista dispositivi e possibilita' di
    # scollegare/revocare QUESTO telefono") -----------------------------------------------------------

    def _handle_list_devices(self):
        """I dispositivi ACCOPPIATI (credential_store, F1.4.6) - non i dispositivi con una sessione attiva ADESSO
        (quelli sono su /status, un concetto diverso: DeviceRegistry e' effimero, questo e' persistente). MAI il
        token, che DeviceIdentity non porta nemmeno (vive solo in DeviceCredential, mai restituito qui)."""
        if self.companion.credential_store is None:
            return self._json_response(404, {"error": "devices_not_configured"})
        devices = [
            {"device_id": d.device_id, "name": d.name, "status": d.status, "created_at": d.created_at,
             "last_seen_at": d.last_seen_at}
            for d in self.companion.credential_store.list_devices()
        ]
        self._json_response(200, {"devices": devices})
        return None

    def _handle_revoke(self, device_id: str):
        """Un dispositivo revoca SOLO se stesso (stesso principio di _handle_claim/_handle_release: mai un altro
        device_id, anche con un token valido) - "scollegare/revocare QUESTO telefono", non un pannello admin che
        revoca dispositivi altrui (fuori scopo per questo MVP). Il token smette di funzionare immediatamente
        (DeviceCredentialStore.verify_token lo controlla gia' a ogni richiesta); l'app locale deve comunque
        cancellare le proprie credenziali salvate - questo endpoint non puo' farlo per lei."""
        if self._read_json_object() is None:
            return None
        if self.companion.credential_store is None:
            return self._reject_body(404, "devices_not_configured")
        if self._device_id_mismatch(device_id):
            return self._reject_body(403, "device_id_mismatch")
        if self._authenticated_device_id is None:
            # Percorso legacy (nessun token per-dispositivo verificato): non c'e' un'identita' autenticata da
            # revocare "se stessa" - stesso principio gia' applicato a _handle_approval per lo stesso motivo.
            return self._reject_body(403, "device_identity_required")
        revoked = self.companion.credential_store.revoke(device_id)
        self._audit("device_revoked", status=200, target_device=device_id, revoked=revoked)
        self._json_response(200, {"revoked": revoked})
        return None

    # ---- pairing (F7.1.2, Companion Mobile MVP) ------------------------------------------

    def _handle_pairing_start(self):
        """Un dispositivo NUOVO (senza credenziali: EndpointClass.PAIRING salta l'autenticazione,
        vedi _prepare) chiede di avviare il pairing. Non crea da sola alcun dispositivo: apre solo
        una `PairingChallenge` effimera e avvisa il canale locale - l'approvazione vera resta un
        "si'" (o la passphrase, se l'utente ne ha configurata una) detto/scritto li', mai un
        secondo passo HTTP raggiungibile dal companion stesso."""
        if self.companion.pairing_service is None:
            self._drain_body()
            return self._json_response(404, {"error": "pairing_not_configured"})
        body = self._read_json_object()
        if body is None:
            return None
        try:
            name = validate_text(body.get("requested_name"), "requested_name", max_chars=MAX_NAME_CHARS)
        except ValidationError as exc:
            return self._reject_body(400, exc.code)
        sync_public_key = body.get("sync_public_key")
        if sync_public_key is not None and not (
            isinstance(sync_public_key, dict) and isinstance(sync_public_key.get("sign"), str)
            and isinstance(sync_public_key.get("agree"), str)
        ):
            return self._reject_body(400, "invalid_sync_public_key")
        challenge = self.companion.pairing_service.start_pairing(requested_name=name)
        self._audit("pairing_started", status=200, challenge_id=challenge.challenge_id, requested_name=name)
        if self.companion.on_pairing_requested is not None:
            try:
                self.companion.on_pairing_requested(challenge, name, sync_public_key)
            except Exception:
                _logger.exception("Errore nel callback on_pairing_requested")
        self._json_response(200, {
            "challenge_id": challenge.challenge_id, "expires_at": challenge.expires_at,
            # F7.2/trust-on-first-use: l'app la mostra all'utente, che la confronta con quella stampata sul PC
            # PRIMA di dire "si'" - null quando il server non ha TLS attivo (bind solo su loopback).
            "tls_fingerprint": self.companion.tls_fingerprint,
        })
        return None

    def _handle_pairing_poll(self, challenge_id: str):
        """`take_result()` e' one-time: un secondo poll DOPO aver gia' ritirato una credenziale non
        la ritrova (la challenge resta comunque `used`, distinta da "mai esistita")."""
        if self.companion.pairing_service is None:
            return self._json_response(404, {"error": "pairing_not_configured"})
        pairing = self.companion.pairing_service
        result = pairing.take_result(challenge_id)
        if isinstance(result, DeviceCredential):
            self._audit("pairing_delivered", status=200, challenge_id=challenge_id, target_device=result.device_id)
            return self._json_response(200, {
                "status": "approved", "device_id": result.device_id, "token": result.token,
                "expires_at": result.expires_at,
            })
        if result == "rejected":
            self._audit("pairing_poll", status=200, challenge_id=challenge_id, pairing_status="rejected")
            return self._json_response(200, {"status": "rejected"})
        challenge = pairing.get_challenge(challenge_id)
        if challenge is None:
            return self._json_response(404, {"error": "unknown_challenge"})
        if not challenge.is_usable():
            status = "expired" if challenge.is_expired() else "already_delivered"
            return self._json_response(200, {"status": status})
        return self._json_response(200, {"status": "pending"})

    # ---- approvazioni di un compito in corso (F6.3/F6.7 -> companion, notifica -> approve/deny) ------------------

    def _handle_approval(self, task_id: str):
        """Risolve la STESSA decisione in sospeso che ha generato la notifica con questo task_id
        (core/task_notification_bridge.py) - riusando integralmente il meccanismo di conferma gia'
        esistente (JakeCore._handle_confirmation via command_handler), mai un secondo percorso di
        esecuzione: "approve"/"deny" diventano lo stesso "si'"/"no" che l'utente digiterebbe in
        chat, sullo stesso canale (device_id autenticato) e sulla stessa conversazione - nessun
        nuovo trace_id, nessuna nuova voce di conversation_state creata apposta."""
        body = self._read_json_object()
        if body is None:
            return None
        decision = body.get("decision")
        if decision not in ("approve", "deny"):
            return self._reject_body(400, "invalid_decision")
        try:
            session_id = validate_identifier(body.get("session_id"), "session_id")
        except ValidationError as exc:
            return self._reject_body(400, exc.code)
        device_id = self._authenticated_device_id
        if device_id is None:
            # Senza un'identita' autenticata non c'e' modo di sapere DI CHI e' la decisione in
            # sospeso da risolvere (percorso legacy/nessun credential_store): niente da indovinare.
            return self._reject_body(403, "device_identity_required")
        if self.companion.conversation_state is None:
            return self._reject_body(404, "no_matching_pending_decision")
        device_token = set_current_device_id(device_id)
        session_token = set_current_session_id(session_id)
        try:
            pending = self.companion.conversation_state.get_pending_action()
            if pending is None or pending.get("trace_id") != task_id:
                return self._reject_body(404, "no_matching_pending_decision")
            text = "sì" if decision == "approve" else "no"
            if not self._audit("approval_received", task_id=task_id, decision=decision, **text_fingerprint(text)):
                return self._reject_body(503, "audit_unavailable")
            started = time.monotonic()
            try:
                response = self.companion.command_handler(text)
            except Exception as exc:
                self._audit(
                    "approval_failed", error=type(exc).__name__, duration_ms=round((time.monotonic() - started) * 1000),
                )
                _logger.exception("Errore nel gestore dell'approvazione companion")
                return self._json_response(500, {"error": "approval_failed"})
        finally:
            reset_current_device_id(device_token)
            reset_current_session_id(session_token)
        self._audit("approval_completed", status=200, duration_ms=round((time.monotonic() - started) * 1000))
        self._json_response(200, {"response": response})
        return None

    def _stream_events(self):
        guard = self.companion.guard
        if not guard.open_stream(self._identity):
            self._reject(429, "too_many_streams")
            return
        self._audit("stream_open", status=200)
        try:
            self._stream_events_locked()
        finally:
            guard.close_stream(self._identity)

    def _stream_events_locked(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        if self._request_id:
            self.send_header("X-Jake-Request-Id", self._request_id)
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

    def _reject_body(self, status: int, error: str):
        """Rifiuto dopo che il corpo e' gia' stato letto: niente drain, stessa audit."""
        self._audit("rejected", status=status, reason=error)
        return self._json_response(status, {"error": error})

    def _read_json_object(self) -> dict | None:
        """Corpo JSON come oggetto ({} se assente). None = richiesta gia' rifiutata (risposta e audit inviati).
        F7.1.5: nessun Transfer-Encoding (non lo leggiamo: rifiutarlo e' meglio che leggere male), Content-Length
        solo cifre e sotto il tetto, JSON valido e di tipo oggetto - prima un corpo sbagliato diventava
        silenziosamente `{}`."""
        if self.headers.get("Transfer-Encoding"):
            self._reject_body(411, "length_required")
            return None
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return {}
        if not raw_length.strip().isdigit():
            self._reject_body(400, "invalid_content_length")
            return None
        length = int(raw_length)
        limit = self.companion.guard.max_body_bytes
        if length > limit:
            self._drain_body()
            self._reject_body(413, "body_too_large")
            return None
        if length == 0:
            return {}
        try:
            raw = self.rfile.read(length)
        except (socket.timeout, TimeoutError):
            self._reject_body(408, "request_timeout")
            return None
        if len(raw) < length:
            self._reject_body(400, "truncated_body")
            return None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._reject_body(400, "invalid_json")
            return None
        if not isinstance(parsed, dict):
            self._reject_body(400, "body_must_be_object")
            return None
        return parsed

    def _json_response(self, status: int, payload: dict, headers: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self._request_id:
            self.send_header("X-Jake-Request-Id", self._request_id)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
