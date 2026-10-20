"""Sonda di Jake Server dal PC Windows (F9.1 presenza, F9.2 capability e protocollo - Distributed / Always-On Jake).

Una sola responsabilita': sapere se il nodo Linux sempre acceso (`jake_server/`) c'e', se parla un protocollo
compatibile e cosa offre. Non sincronizza, non inoltra comandi, non sposta dati: le fasi successive decideranno se
usare un servizio del server con `supports(nome)`, che e' vero solo con il nodo ONLINE e il nome tra le capability.

Jake Windows non dipende mai da questo modulo per funzionare:
- senza `server_url` (config) / `JAKE_SERVER_URL` (ambiente) non parte nessuna richiesta (stato DISABLED);
- `probe()` non solleva MAI: timeout, DNS sbagliato, porta chiusa, risposta strana diventano uno stato;
- nessuna richiesta all'import; all'avvio e nei refresh la sonda gira su un thread daemon breve, uno alla volta:
  JakeCore non aspetta mai la rete.

Una sonda = `GET /health` (vivo e sano?) poi `GET /capabilities` (chi sei, che protocollo, cosa offri). Stati:
- ONLINE: entrambe le risposte sono di un Jake Server sano e la finestra di protocollo e' compatibile.
- DEGRADED: all'indirizzo risponde qualcosa, ma non un Jake Server utilizzabile: HTTP non 200, JSON malformato,
  servizio diverso, status diverso da "ok", redirect, errore TLS, `/capabilities` assente (server F9.1 non
  aggiornato) o protocollo incompatibile. Distingue "server spento" da "server da aggiornare/configurare".
- OFFLINE: nessuna risposta (timeout, connessione rifiutata, nome non risolto, rete assente).
- DISABLED: nessun URL configurato, o URL non valido.

Compatibilita' (F9.2): stessa finestra del server companion (`core/companion_guard.py`, MIN_PROTOCOL_VERSION..
PROTOCOL_VERSION). Il server pubblica la sua (`min_protocol_version`..`protocol_version`): compatibili se le due
finestre si sovrappongono.

Cache e backoff (F9.2): lo stato resta in memoria (`last_status`). `refresh_if_due()` - chiamato a ogni turno da
JakeCore, costa un confronto di orologio - avvia una sonda solo quando quella precedente e' scaduta: dopo
ONLINE_RECHECK_SECONDS se il nodo era ONLINE, altrimenti dopo un'attesa che raddoppia a ogni fallimento consecutivo da
RETRY_INITIAL_SECONDS fino a RETRY_MAX_SECONDS. Niente thread permanenti, niente polling quando Jake non e' usato.

Limiti dichiarati: il timeout di urllib copre connessione e lettura, non la risoluzione DNS di un NOME (un nome
inesistente puo' far aspettare qualche secondo il sistema operativo) - per questo le sonde del runtime sono sempre in
background. HTTPS verifica il certificato come sempre: un certificato non valido e' DEGRADED, mai accettato."""
from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from urllib import error, request
from urllib.parse import urlsplit

from core.companion_guard import MIN_PROTOCOL_VERSION
from core.logger import get_logger
from core.version import PROTOCOL_VERSION

SERVICE_NAME = "jake-server"
HEALTH_PATH = "/health"
CAPABILITIES_PATH = "/capabilities"
DEFAULT_TIMEOUT_SECONDS = 1.5
MIN_TIMEOUT_SECONDS = 0.2
MAX_TIMEOUT_SECONDS = 5.0
# /health e /capabilities rispondono con poche decine di byte: oltre questo limite non e' un Jake Server.
MAX_RESPONSE_BYTES = 16 * 1024
ONLINE_RECHECK_SECONDS = 60.0
RETRY_INITIAL_SECONDS = 15.0
RETRY_MAX_SECONDS = 300.0
MAX_CAPABILITIES = 32
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")

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
    protocol_version: int | None = None
    capabilities: tuple[str, ...] = ()
    # Ora (time.time) dell'ultima sonda ONLINE, anche se questa non lo e': "da quanto il server manca".
    last_success_at: float | None = None

    def as_dict(self, now: float | None = None) -> dict:
        """Forma condivisibile (diagnostica): nessun URL, IP o dato dell'utente, solo stato e cio' che il server dice
        di se'. Le eta' in secondi invece degli orari assoluti."""
        now = time.time() if now is None else now
        return {
            "state": self.state.value,
            "reason": self.reason,
            "version": self.version,
            "protocol_version": self.protocol_version,
            "latency_ms": self.latency_ms,
            "capabilities": list(self.capabilities),
            "checked_age_s": round(max(0.0, now - self.checked_at), 1),
            "last_success_age_s": (round(max(0.0, now - self.last_success_at), 1)
                                   if self.last_success_at is not None else None),
        }


class _NoRedirect(request.HTTPRedirectHandler):
    """Un redirect non e' un Jake Server: si ferma al primo 3xx invece di seguirlo altrove."""

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


def _positive_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def protocol_compatible(server_max: int, server_min: int) -> bool:
    """Le due finestre [min, max] di protocollo si sovrappongono."""
    return server_min <= PROTOCOL_VERSION and MIN_PROTOCOL_VERSION <= server_max


def _parse_capabilities(raw) -> tuple[str, ...] | None:
    """Nomi brevi e prevedibili, senza duplicati; None se la lista non ha la forma attesa."""
    if not isinstance(raw, list) or len(raw) > MAX_CAPABILITIES:
        return None
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not _CAPABILITY_RE.match(item):
            return None
        if item not in names:
            names.append(item)
    return tuple(names)


class JakeServerClient:
    """`base_url` es. "http://<ip-del-server>:8770" (mai scritto nel codice: arriva da config/ambiente).
    `opener`, `clock` (monotono: latenze e scadenze) e `wall` (orari degli stati) sono iniettabili per i test."""

    def __init__(self, base_url: str | None, timeout: float = DEFAULT_TIMEOUT_SECONDS, opener=None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.configured = bool(isinstance(base_url, str) and base_url.strip())
        self.base_url = _normalize_url(base_url)
        self.timeout = _clamp_timeout(timeout)
        # ProxyHandler({}): il server e' in LAN, un proxy di sistema non deve vederne il traffico ne' mediarlo.
        self._opener = opener or request.build_opener(request.ProxyHandler({}), _NoRedirect())
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._last = ServerStatus(ServerState.DISABLED, "not_checked", checked_at=wall())
        self._failures = 0
        self._next_due = 0.0
        self._in_flight = False
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

    def supports(self, capability: str) -> bool:
        """Il nodo e' ONLINE e dichiara questa capability: l'unica condizione per usarne il servizio."""
        status = self.last_status
        return status.state == ServerState.ONLINE and capability in status.capabilities

    def seconds_until_next_probe(self) -> float | None:
        """None se non ci sara' mai una sonda (nessun URL valido); 0 se e' gia' dovuta."""
        if not self.enabled:
            return None
        with self._lock:
            return max(0.0, self._next_due - self._clock())

    def probe(self) -> ServerStatus:
        """Una sonda sincrona (diagnostica, test). Non solleva mai: ogni guasto diventa uno stato."""
        try:
            status = self._probe()
        except Exception:  # rete di sicurezza: nessun guasto della sonda deve arrivare al runtime
            _logger.debug("Sonda Jake Server fallita in modo inatteso", exc_info=True)
            status = ServerStatus(ServerState.OFFLINE, "error")
        return self._remember(status)

    def probe_in_background(self, on_result: Callable[[ServerStatus], None] | None = None) -> threading.Thread | None:
        """Sonda su un thread daemon. None (nessun thread) se il server non e' configurato o una sonda e' gia' in
        corso: mai due richieste contemporanee verso lo stesso server."""
        if not self.enabled:
            self._remember(ServerStatus(ServerState.DISABLED, "not_configured" if not self.configured else "invalid_url"))
            return None
        with self._lock:
            if self._in_flight:
                return None
            self._in_flight = True

        def _run() -> None:
            try:
                status = self.probe()
            finally:
                with self._lock:
                    self._in_flight = False
            if on_result is not None:
                try:
                    on_result(status)
                except Exception:
                    _logger.exception("Callback della sonda Jake Server fallita")

        thread = threading.Thread(target=_run, name="jake-server-probe", daemon=True)
        try:
            thread.start()
        except RuntimeError:  # thread esauriti: si riprova al prossimo refresh, Jake prosegue
            with self._lock:
                self._in_flight = False
            _logger.debug("Impossibile avviare la sonda Jake Server", exc_info=True)
            return None
        return thread

    def refresh_if_due(self) -> threading.Thread | None:
        """Sonda in background solo se quella precedente e' scaduta (cache/backoff, vedi il docstring del modulo).
        Pensata per il percorso caldo: senza URL o prima della scadenza ritorna subito senza fare nulla."""
        if not self.enabled:
            return None
        with self._lock:
            if self._in_flight or self._clock() < self._next_due:
                return None
        return self.probe_in_background()

    # ---- sonda ---------------------------------------------------------------------------------------------------

    def _probe(self) -> ServerStatus:
        if self.base_url is None:
            return ServerStatus(ServerState.DISABLED, "not_configured" if not self.configured else "invalid_url")
        started = self._clock()
        failure, health = self._get_json(HEALTH_PATH, started)
        if failure is not None:
            return failure
        assert health is not None
        if health.get("service") != SERVICE_NAME:
            return ServerStatus(ServerState.DEGRADED, "unexpected_service", latency_ms=self._elapsed(started))
        version = health.get("version")
        version = version[:32] if isinstance(version, str) else None
        if health.get("status") != "ok":
            return ServerStatus(ServerState.DEGRADED, "unhealthy", version=version, latency_ms=self._elapsed(started))

        failure, described = self._get_json(CAPABILITIES_PATH, started)
        if failure is not None:
            # Un server F9.1 risponde 404: vivo, ma non dice ne' protocollo ne' capability - da aggiornare.
            reason = "capabilities_unavailable" if failure.reason == "http_404" else failure.reason
            return replace(failure, reason=reason, version=version)
        assert described is not None
        latency = self._elapsed(started)
        if described.get("service") != SERVICE_NAME:
            return ServerStatus(ServerState.DEGRADED, "unexpected_service", version=version, latency_ms=latency)
        server_max = _positive_int(described.get("protocol_version"))
        server_min = _positive_int(described.get("min_protocol_version", server_max))
        capabilities = _parse_capabilities(described.get("capabilities"))
        if server_max is None or server_min is None or server_min > server_max or capabilities is None:
            return ServerStatus(ServerState.DEGRADED, "malformed_response", version=version, latency_ms=latency)
        if not protocol_compatible(server_max, server_min):
            return ServerStatus(ServerState.DEGRADED, "protocol_incompatible", version=version, latency_ms=latency,
                                protocol_version=server_max, capabilities=capabilities)
        return ServerStatus(ServerState.ONLINE, "ok", version=version, latency_ms=latency,
                            protocol_version=server_max, capabilities=capabilities)

    def _get_json(self, path: str, started: float) -> tuple[ServerStatus | None, dict | None]:
        """(stato di fallimento, None) oppure (None, oggetto JSON) per GET `path`."""
        assert self.base_url is not None
        req = request.Request(self.base_url + path, headers={"Accept": "application/json"}, method="GET")
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                status_code = response.status
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            exc.close()
            return ServerStatus(ServerState.DEGRADED, f"http_{exc.code}", latency_ms=self._elapsed(started)), None
        except error.URLError as exc:
            return self._network_failure(exc.reason, started), None
        except http.client.HTTPException:
            # Ha risposto qualcosa che non e' HTTP valido (o ha chiuso senza rispondere): non un Jake Server sano.
            return ServerStatus(ServerState.DEGRADED, "malformed_response", latency_ms=self._elapsed(started)), None
        except OSError as exc:
            return self._network_failure(exc, started), None
        if status_code != 200:
            return ServerStatus(ServerState.DEGRADED, f"http_{status_code}", latency_ms=self._elapsed(started)), None
        malformed = ServerStatus(ServerState.DEGRADED, "malformed_response", latency_ms=self._elapsed(started))
        if len(body) > MAX_RESPONSE_BYTES:
            return malformed, None
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return malformed, None
        if not isinstance(data, dict):
            return malformed, None
        return None, data

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

    def _elapsed(self, started: float) -> float:
        return round((self._clock() - started) * 1000, 1)

    # ---- cache, backoff, log -------------------------------------------------------------------------------------

    def _remember(self, status: ServerStatus) -> ServerStatus:
        now = self._wall()
        with self._lock:
            previous = self._last
            online = status.state == ServerState.ONLINE
            status = replace(status, checked_at=now, last_success_at=now if online else previous.last_success_at)
            self._last = status
            if status.state == ServerState.DISABLED:
                self._next_due = float("inf")
            elif online:
                self._failures = 0
                self._next_due = self._clock() + ONLINE_RECHECK_SECONDS
            else:
                self._failures += 1
                delay = min(RETRY_MAX_SECONDS, RETRY_INITIAL_SECONDS * 2 ** min(self._failures - 1, 16))
                self._next_due = self._clock() + delay
        if (previous.state, previous.reason) != (status.state, status.reason):
            self._log_change(status)
        return status

    def _log_change(self, status: ServerStatus) -> None:
        if status.state == ServerState.ONLINE:
            _logger.info("Jake Server raggiungibile su %s (versione %s, protocollo %s, capability %s, %.0f ms)",
                         self.base_url, status.version or "?", status.protocol_version,
                         ",".join(status.capabilities) or "-", status.latency_ms or 0)
        elif status.state in (ServerState.OFFLINE, ServerState.DEGRADED):
            # INFO, non WARNING: il server e' facoltativo, Jake continua in locale.
            _logger.info("Jake Server %s su %s (%s): Jake continua in locale",
                         status.state.value, self.base_url, status.reason)
