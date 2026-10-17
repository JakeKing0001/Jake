"""Sonda di Jake Server dal PC Windows (F9.1, Distributed / Always-On Jake).

Una sola responsabilita': sapere se il nodo Linux sempre acceso (`jake_server/`) c'e' e risponde come un Jake
Server sano. Non sincronizza, non inoltra comandi, non sposta dati: e' il primo mattone su cui le fasi successive
decideranno se usare il server (`is_available`).

Jake Windows non dipende mai da questo modulo per funzionare:
- senza `server_url` (config) / `JAKE_SERVER_URL` (ambiente) non parte nessuna richiesta (stato DISABLED);
- `probe()` non solleva MAI: timeout, DNS sbagliato, porta chiusa, risposta strana diventano uno stato;
- all'avvio la sonda gira su un thread daemon (`probe_in_background`): JakeCore non aspetta la rete.

Stati:
- ONLINE: `/health` ha risposto 200 con il JSON di un Jake Server e `status == "ok"`.
- DEGRADED: all'indirizzo risponde qualcosa, ma non un Jake Server sano (HTTP non 2xx, JSON malformato, servizio
  diverso, status diverso da "ok", errore TLS). Utile a distinguere "server spento" da "configurazione sbagliata".
- OFFLINE: nessuna risposta (timeout, connessione rifiutata, nome non risolto, rete assente).
- DISABLED: nessun URL configurato, o URL non valido.

Limiti dichiarati: il timeout di urllib copre connessione e lettura, non la risoluzione DNS di un NOME (un nome
inesistente puo' far aspettare qualche secondo il sistema operativo) - per questo la sonda di avvio e' sempre in
background. HTTPS verifica il certificato come sempre: un certificato non valido e' DEGRADED, mai accettato."""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from urllib import error, request
from urllib.parse import urlsplit

from core.logger import get_logger

SERVICE_NAME = "jake-server"
HEALTH_PATH = "/health"
DEFAULT_TIMEOUT_SECONDS = 1.5
MIN_TIMEOUT_SECONDS = 0.2
MAX_TIMEOUT_SECONDS = 5.0
# /health risponde con poche decine di byte: oltre questo limite non e' un Jake Server (e non lo si legge tutto).
MAX_RESPONSE_BYTES = 16 * 1024

_logger = get_logger("jake_server_client")


class ServerState(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ServerStatus:
    state: ServerState
    reason: str
    version: str | None = None
    latency_ms: float | None = None
    checked_at: float = field(default_factory=time.time)


class _NoRedirect(request.HTTPRedirectHandler):
    """Un redirect da /health non e' un Jake Server: si ferma al primo 3xx invece di seguirlo altrove."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _normalize_url(raw) -> str | None:
    """URL base senza slash finale, o None se assente/non valido. Credenziali nell'URL rifiutate: finirebbero nei log."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    url = raw.strip().rstrip("/")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - solleva ValueError se la porta non e' un numero valido
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    if parts.query or parts.fragment:
        return None
    return url


def _clamp_timeout(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    if value != value:  # NaN
        return DEFAULT_TIMEOUT_SECONDS
    return min(MAX_TIMEOUT_SECONDS, max(MIN_TIMEOUT_SECONDS, value))


def _is_timeout(exc: BaseException | None) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout))


class JakeServerClient:
    """`base_url` es. "http://<ip-del-server>:8770" (mai scritto nel codice: arriva da config/ambiente).
    `opener` e `clock` sono iniettabili per i test."""

    def __init__(self, base_url: str | None, timeout: float = DEFAULT_TIMEOUT_SECONDS, opener=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.configured = bool(isinstance(base_url, str) and base_url.strip())
        self.base_url = _normalize_url(base_url)
        self.timeout = _clamp_timeout(timeout)
        # ProxyHandler({}): il server e' in LAN, un proxy di sistema non deve vederne il traffico ne' mediarlo.
        self._opener = opener or request.build_opener(request.ProxyHandler({}), _NoRedirect())
        self._clock = clock
        self._lock = threading.Lock()
        self._last = ServerStatus(ServerState.DISABLED, "not_checked")
        if self.configured and self.base_url is None:
            _logger.warning("server_url/JAKE_SERVER_URL non valido: Jake Server ignorato (serve http(s)://host:porta)")

    @classmethod
    def from_config(cls, config) -> "JakeServerClient":
        """`server_url` -> JAKE_SERVER_URL, `server_timeout_seconds` -> JAKE_SERVER_TIMEOUT_SECONDS (Config.get)."""
        return cls(config.get("server_url"), timeout=config.get("server_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

    @property
    def enabled(self) -> bool:
        return self.base_url is not None

    @property
    def last_status(self) -> ServerStatus:
        with self._lock:
            return self._last

    @property
    def is_available(self) -> bool:
        return self.last_status.state == ServerState.ONLINE

    def probe(self) -> ServerStatus:
        """Interroga `/health` una volta. Non solleva mai: ogni guasto diventa uno stato."""
        try:
            status = self._probe()
        except Exception:  # rete di sicurezza: nessun guasto della sonda deve arrivare al runtime
            _logger.debug("Sonda Jake Server fallita in modo inatteso", exc_info=True)
            status = ServerStatus(ServerState.OFFLINE, "error")
        self._remember(status)
        return status

    def probe_in_background(self, on_result: Callable[[ServerStatus], None] | None = None) -> threading.Thread | None:
        """Sonda su un thread daemon; None (nessun thread) se il server non e' configurato."""
        if not self.enabled:
            self._remember(ServerStatus(ServerState.DISABLED, "not_configured" if not self.configured else "invalid_url"))
            return None

        def _run() -> None:
            status = self.probe()
            if on_result is not None:
                try:
                    on_result(status)
                except Exception:
                    _logger.exception("Callback della sonda Jake Server fallita")

        thread = threading.Thread(target=_run, name="jake-server-probe", daemon=True)
        thread.start()
        return thread

    def _probe(self) -> ServerStatus:
        if self.base_url is None:
            return ServerStatus(ServerState.DISABLED, "not_configured" if not self.configured else "invalid_url")
        req = request.Request(self.base_url + HEALTH_PATH, headers={"Accept": "application/json"}, method="GET")
        started = self._clock()
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                status_code = response.status
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            exc.close()
            return ServerStatus(ServerState.DEGRADED, f"http_{exc.code}", latency_ms=self._elapsed(started))
        except error.URLError as exc:
            return self._network_failure(exc.reason, started)
        except http.client.HTTPException:
            # Ha risposto qualcosa che non e' HTTP valido (o ha chiuso senza rispondere): non un Jake Server sano.
            return ServerStatus(ServerState.DEGRADED, "malformed_response", latency_ms=self._elapsed(started))
        except OSError as exc:
            return self._network_failure(exc, started)
        latency = self._elapsed(started)
        if status_code != 200:
            return ServerStatus(ServerState.DEGRADED, f"http_{status_code}", latency_ms=latency)
        return self._parse(body, latency)

    def _network_failure(self, reason, started: float) -> ServerStatus:
        latency = self._elapsed(started)
        if _is_timeout(reason):
            return ServerStatus(ServerState.OFFLINE, "timeout", latency_ms=latency)
        if isinstance(reason, ssl.SSLError):
            return ServerStatus(ServerState.DEGRADED, "tls_error", latency_ms=latency)
        if isinstance(reason, socket.gaierror):
            return ServerStatus(ServerState.OFFLINE, "unresolved_host", latency_ms=latency)
        if isinstance(reason, ConnectionRefusedError):
            return ServerStatus(ServerState.OFFLINE, "refused", latency_ms=latency)
        return ServerStatus(ServerState.OFFLINE, "unreachable", latency_ms=latency)

    @staticmethod
    def _parse(body: bytes, latency: float) -> ServerStatus:
        if len(body) > MAX_RESPONSE_BYTES:
            return ServerStatus(ServerState.DEGRADED, "malformed_response", latency_ms=latency)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return ServerStatus(ServerState.DEGRADED, "malformed_response", latency_ms=latency)
        if not isinstance(data, dict):
            return ServerStatus(ServerState.DEGRADED, "malformed_response", latency_ms=latency)
        if data.get("service") != SERVICE_NAME:
            return ServerStatus(ServerState.DEGRADED, "unexpected_service", latency_ms=latency)
        version = data.get("version")
        version = version[:32] if isinstance(version, str) else None
        if data.get("status") != "ok":
            return ServerStatus(ServerState.DEGRADED, "unhealthy", version=version, latency_ms=latency)
        return ServerStatus(ServerState.ONLINE, "ok", version=version, latency_ms=latency)

    def _elapsed(self, started: float) -> float:
        return round((self._clock() - started) * 1000, 1)

    def _remember(self, status: ServerStatus) -> None:
        with self._lock:
            previous = self._last
            self._last = status
        if (previous.state, previous.reason) == (status.state, status.reason):
            return
        if status.state == ServerState.ONLINE:
            _logger.info("Jake Server raggiungibile su %s (versione %s, %.0f ms)",
                         self.base_url, status.version or "?", status.latency_ms or 0)
        elif status.state in (ServerState.OFFLINE, ServerState.DEGRADED):
            # INFO, non WARNING: il server e' facoltativo, Jake continua in locale.
            _logger.info("Jake Server %s su %s (%s): Jake continua in locale",
                         status.state.value, self.base_url, status.reason)
