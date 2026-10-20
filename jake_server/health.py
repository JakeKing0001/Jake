"""Endpoint di sola lettura di Jake Server: `GET /health` (F9.1), `GET /capabilities` (F9.2) e nient'altro.

Solo libreria standard (`http.server`), come `core/companion_server.py`: nessun framework in piu' da installare
sul server per due endpoint fissi.

Perche' qui non vale `check_bind_policy` (F7.1.4) e il server ascolta in LAN senza TLS: quella regola vieta un bind
non locale in chiaro **per i comandi**, perche' token e testo viaggerebbero leggibili. Questo server non ha comandi
ne' credenziali: non legge mai un corpo, non accetta altri metodi o percorsi e risponde con un JSON fisso senza dati
personali (servizio, versione di Jake e del protocollo, ruolo, uptime, nomi delle capability). Il confine resta voluto e verificato dai test: qualunque endpoint
futuro che legga o cambi dati (sync, memoria, comandi) va servito dalla pipeline di `core/companion_server.py`
(autenticazione, capability, anti-replay, TLS), non aggiunto a questa tabella.

Difese comunque presenti, riusate da `core/companion_guard.py`: rate limit per indirizzo (lo stesso token bucket
dei client companion, classe READ_ONLY) e timeout breve su ogni connessione (un client che apre e tace non tiene
occupato un thread). L'intestazione `Server` non rivela la versione di Python."""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from core.companion_guard import MIN_PROTOCOL_VERSION, EndpointClass, RateLimiter
from core.version import PROTOCOL_VERSION, VERSION
from jake_server import CAPABILITIES, DEFAULT_PORT, NODE_ROLE, SERVICE_NAME

HEALTH_PATH = "/health"
CAPABILITIES_PATH = "/capabilities"
REQUEST_TIMEOUT_SECONDS = 5

_logger = logging.getLogger("jake_server")


def health_payload(started_at: float, now: float) -> dict:
    """Il corpo di `/health`. Nessun nome host, IP, percorso o dato dell'utente: chiunque in LAN puo' leggerlo."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "node": NODE_ROLE,
        "uptime_s": max(0, int(now - started_at)),
    }


def capabilities_payload() -> dict:
    """Il corpo di `/capabilities` (F9.2): chi e' il nodo, quale finestra di protocollo parla e cosa offre. La finestra
    e' la stessa del server companion (`core/companion_guard.py`): un client e' compatibile se la sua si sovrappone."""
    return {
        "service": SERVICE_NAME,
        "node": NODE_ROLE,
        "version": VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "min_protocol_version": MIN_PROTOCOL_VERSION,
        "capabilities": list(CAPABILITIES),
    }


class HealthServer:
    """Server HTTP minimo. `port=0` lascia scegliere una porta libera al sistema operativo (test)."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, rate_limiter: RateLimiter | None = None,
                 clock=time.monotonic) -> None:
        self.host = host
        self.port = port
        self.rate_limiter = rate_limiter or RateLimiter()
        self._clock = clock
        self._started_at = clock()
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def payload(self) -> dict:
        return health_payload(self._started_at, self._clock())

    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = _Server((self.host, self.port), _Handler, self)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="jake-server-health", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._httpd = None
        self._thread = None


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler_cls, health: HealthServer) -> None:
        self.health = health
        super().__init__(address, handler_cls)

    def handle_error(self, request, client_address):
        # Un client che chiude a meta' o va in timeout non e' un errore del server: niente traceback su stderr.
        _logger.debug("Richiesta da %s terminata con errore", client_address, exc_info=True)


class _Handler(BaseHTTPRequestHandler):
    server_version = SERVICE_NAME
    timeout = REQUEST_TIMEOUT_SECONDS

    def version_string(self):  # di default "BaseHTTP/0.6 Python/3.x.y": nessuna versione da regalare a chi scansiona
        return SERVICE_NAME

    def log_message(self, format, *args):  # il log di http.server scriverebbe ogni richiesta su stderr
        pass

    @property
    def health(self) -> HealthServer:
        return cast(_Server, self.server).health

    def do_GET(self):
        self._respond(include_body=True)

    def do_HEAD(self):
        self._respond(include_body=False)

    def _respond(self, include_body: bool) -> None:
        ok, wait = self.health.rate_limiter.allow(f"ip:{self.client_address[0]}", EndpointClass.READ_ONLY)
        if not ok:
            self._json(429, {"error": "rate_limited"}, include_body, {"Retry-After": str(max(1, int(wait + 0.999)))})
            return
        path = self.path.split("?", 1)[0]
        if path == HEALTH_PATH:
            self._json(200, self.health.payload(), include_body)
        elif path == CAPABILITIES_PATH:
            self._json(200, capabilities_payload(), include_body)
        else:
            self._json(404, {"error": "not_found"}, include_body)

    def _json(self, status: int, payload: dict, include_body: bool, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if include_body:
            self.wfile.write(body)
