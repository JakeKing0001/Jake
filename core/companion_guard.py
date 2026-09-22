"""Difese del server companion (F7.1.1, F7.1.4-F7.1.7): tutto cio' che sta PRIMA di un comando.

`core/companion_server.py` sa gia' chi e' il chiamante (token per dispositivo, F1.4.6). Questo modulo decide
se, in quel momento e in quella forma, la richiesta e' accettabile - senza rete, con orologi iniettabili, cosi'
ogni regola si prova con numeri esatti e non con `sleep`.

- `EndpointClass` / `classify`: read-only, command, approval, file, audio sono classi DISTINTE (F7.1.7). Una
  capability e' un insieme di classi: un dispositivo che puo' solo guardare (READ_ONLY) non arriva mai al
  gestore dei comandi, anche con un token valido.
- `RateLimiter`: token bucket per (identita', classe) con burst e ricarica; `AuthFailureThrottle`: dopo N
  autenticazioni fallite da uno stesso indirizzo, blocco per un po' SENZA nemmeno valutare il token (altrimenti
  il blocco non fermerebbe chi indovina un carattere alla volta).
- `ReplayGuard`: timestamp entro una finestra + nonce mai visto; la cache dei nonce ha un tetto e, piena, RIFIUTA
  invece di espellere (espellere un nonce ancora nella finestra riaprirebbe il replay).
- `validate_*`: tipi, lunghezze e caratteri di ogni campo (F7.1.5) - un campo del tipo sbagliato e' un 400, mai
  un'eccezione.
- `CompanionAudit`: registro JSONL append-only con catena di hash (F7.1.6). Registra chi, quando, quale
  endpoint, esito e MOTIVO del rifiuto; del testo di un comando conserva solo lunghezza e SHA-256 (un comando
  puo' contenere una password detta a voce: il registro non deve diventare un secondo posto dove trovarla).
  La catena rileva modifiche e cancellazioni a meta' file, non la cancellazione dell'ULTIMA riga: chi vuole
  garanzie piu' forti deve ancorare `head()` altrove.
- `check_bind_policy` (F7.1.4): un bind non-loopback senza TLS e' vietato per costruzione.

Limiti dichiarati: la capability per dispositivo vive in memoria del server (non e' nel database delle
credenziali: un riavvio la riporta al default); niente firma del corpo con chiave condivisa (senza TLS un
attaccante che intercetta vede token e nonce - per questo su LAN il TLS e' obbligatorio, non opzionale)."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from core.version import PROTOCOL_VERSION

DEFAULT_AUDIT_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_companion_audit.jsonl"

# F7.1.1: quanti protocolli indietro il server accetta ancora un client. 1 = il corrente e il precedente.
PROTOCOL_COMPAT_WINDOW = 1
MIN_PROTOCOL_VERSION = max(1, PROTOCOL_VERSION - PROTOCOL_COMPAT_WINDOW)

MAX_BODY_BYTES = 64 * 1024
MAX_TEXT_CHARS = 4000
MAX_NAME_CHARS = 80
REPLAY_WINDOW_SECONDS = 120.0
REPLAY_CACHE_LIMIT = 20000
MAX_STREAMS_PER_IDENTITY = 4

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class EndpointClass(str, Enum):
    READ_ONLY = "read_only"
    COMMAND = "command"
    APPROVAL = "approval"
    FILE = "file"
    AUDIO = "audio"
    # F7.1.2/Companion Mobile MVP: un dispositivo NUOVO non ha ancora una credenziale - per
    # costruzione non passa mai da _authenticate() (vedi _Handler._prepare in
    # core/companion_server.py). La sicurezza non viene da un token ma dalla conferma esplicita
    # sul PC (core/pairing_service.py): questa classe resta comunque protetta da rate limit (per
    # indirizzo, l'unica identita' disponibile prima del pairing) e dal resto della pipeline del
    # corpo/audit, solo non dall'autenticazione ne' dalla capability per-dispositivo.
    PAIRING = "pairing"


# Capability di default per un dispositivo che non ne ha una esplicita: le tre classi che esistono davvero oggi.
# FILE e AUDIO NON sono nel default: sono le piu' sensibili e nessun endpoint le usa ancora - quando arriveranno
# vanno concesse una per una, non ereditate.
DEFAULT_CAPABILITIES = frozenset({EndpointClass.READ_ONLY, EndpointClass.COMMAND, EndpointClass.APPROVAL})


def classify(method: str, path: str) -> EndpointClass | None:
    """Classe dell'endpoint, o None se il percorso non e' noto (il server risponde 404 DOPO l'autenticazione).
    I prefissi /approvals/, /files/ e /audio/ sono riservati: un endpoint futuro li eredita con la classe giusta
    senza poter "dimenticare" i controlli. /pairing/ e' l'unico prefisso deliberatamente RAGGIUNGIBILE senza
    autenticazione (vedi EndpointClass.PAIRING)."""
    method = method.upper()
    path = path.split("?", 1)[0]
    if method == "GET" and path in ("/status", "/events"):
        return EndpointClass.READ_ONLY
    if method == "GET" and path.startswith("/pairing/"):
        return EndpointClass.PAIRING
    if method == "POST":
        if path == "/command":
            return EndpointClass.COMMAND
        if path.startswith("/devices/") and (path.endswith("/claim") or path.endswith("/release")):
            return EndpointClass.COMMAND  # cambia la sessione attiva: e' un'azione, non una lettura
        if path == "/pairing/start":
            return EndpointClass.PAIRING
        if path.startswith("/approvals/"):
            return EndpointClass.APPROVAL
        if path.startswith("/files/"):
            return EndpointClass.FILE
        if path.startswith("/audio/"):
            return EndpointClass.AUDIO
    return None


# ---- policy di bind (F7.1.4) ------------------------------------------------------------------------------------


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # "", "0.0.0.0" (che non e' un IP valido per is_loopback) o un nome host: non presumibile locale


class InsecureBindError(RuntimeError):
    """Bind su un'interfaccia non locale senza TLS: vietato per i comandi."""


def check_bind_policy(host: str, has_tls: bool) -> None:
    if not is_loopback_host(host) and not has_tls:
        raise InsecureBindError(
            f"bind su {host or '<tutte le interfacce>'} senza TLS vietato: su una rete locale token e comandi "
            "viaggerebbero in chiaro. Passa un tls_context, o ascolta solo su 127.0.0.1.")


# ---- limiti di frequenza ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limit:
    burst: float
    per_second: float


DEFAULT_LIMITS: dict[EndpointClass, Limit] = {
    EndpointClass.READ_ONLY: Limit(60, 20),
    EndpointClass.COMMAND: Limit(20, 2),
    EndpointClass.APPROVAL: Limit(10, 1),
    EndpointClass.FILE: Limit(5, 0.5),
    EndpointClass.AUDIO: Limit(20, 5),
    # Piu' stretto di ogni altra classe: non autenticato, quindi l'unico freno prima della
    # conferma esplicita sul PC e' questo - un indirizzo non deve poter aprire una raffica di
    # richieste di pairing.
    EndpointClass.PAIRING: Limit(5, 0.1),
}


class RateLimiter:
    """Token bucket per (identita', classe). `allow` ritorna (ok, secondi da attendere)."""

    def __init__(self, limits: dict[EndpointClass, Limit] | None = None, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 5000) -> None:
        self._limits = dict(DEFAULT_LIMITS)
        if limits:
            self._limits.update(limits)
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: dict[tuple[str, EndpointClass], tuple[float, float]] = {}  # chiave -> (gettoni, ultimo istante)
        self._lock = threading.Lock()

    def allow(self, identity: str, cls: EndpointClass) -> tuple[bool, float]:
        limit = self._limits[cls]
        now = self._clock()
        key = (identity, cls)
        with self._lock:
            tokens, last = self._buckets.get(key, (limit.burst, now))
            tokens = min(limit.burst, tokens + (now - last) * limit.per_second)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                self._trim()
                return True, 0.0
            self._buckets[key] = (tokens, now)
            self._trim()
            return False, (1.0 - tokens) / limit.per_second if limit.per_second > 0 else float("inf")

    def _trim(self) -> None:
        # Un attaccante che cambia identita' (indirizzo) a ogni richiesta non deve far crescere la mappa senza limite.
        if len(self._buckets) > self._max_keys:
            for key in list(self._buckets)[: len(self._buckets) - self._max_keys]:
                del self._buckets[key]


class AuthFailureThrottle:
    """Dopo `max_failures` autenticazioni fallite in `window` secondi da una stessa chiave (l'indirizzo), la chiave
    e' bloccata per `lockout` secondi: le sue richieste non vengono nemmeno autenticate. Limite noto: su
    127.0.0.1 tutti i processi locali condividono l'indirizzo, quindi un processo locale ostile puo' bloccare per
    un minuto il client legittimo - ma un processo locale ostile ha gia' modi piu' diretti di farti del male."""

    def __init__(self, max_failures: int = 8, window: float = 60.0, lockout: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._max, self._window, self._lockout, self._clock = max_failures, window, lockout, clock
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> float:
        """Secondi di blocco rimasti (0 = non bloccata)."""
        now = self._clock()
        with self._lock:
            until = self._locked_until.get(key, 0.0)
            if until <= now:
                self._locked_until.pop(key, None)
                return 0.0
            return until - now

    def record_failure(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            events = self._failures.setdefault(key, deque())
            events.append(now)
            while events and events[0] < now - self._window:
                events.popleft()
            if len(events) >= self._max:
                self._locked_until[key] = now + self._lockout
                events.clear()
            if len(self._failures) > 5000:
                self._failures.pop(next(iter(self._failures)))


# ---- replay ----------------------------------------------------------------------------------------------------------


class ReplayGuard:
    """`check` ritorna None se la richiesta e' fresca, altrimenti il codice dell'errore."""

    def __init__(self, window: float = REPLAY_WINDOW_SECONDS, limit: int = REPLAY_CACHE_LIMIT,
                 clock: Callable[[], float] = time.time) -> None:
        self._window, self._limit, self._clock = window, limit, clock
        self._seen: dict[str, float] = {}  # "identita'|nonce" -> scadenza
        self._lock = threading.Lock()

    def check(self, identity: str, timestamp: str | None, nonce: str | None, required: bool) -> str | None:
        if timestamp is None and nonce is None:
            return "replay_headers_required" if required else None
        if timestamp is None or nonce is None:
            return "replay_headers_incomplete"
        if not _NONCE_RE.match(nonce):
            return "invalid_nonce"
        try:
            sent = float(timestamp)
        except ValueError:
            return "invalid_timestamp"
        if sent != sent or sent in (float("inf"), float("-inf")):  # NaN / inf
            return "invalid_timestamp"
        now = self._clock()
        if abs(now - sent) > self._window:
            return "stale_timestamp"
        key = f"{identity}|{nonce}"
        with self._lock:
            for old in [k for k, exp in self._seen.items() if exp <= now]:
                del self._seen[old]
            if key in self._seen:
                return "replayed_nonce"
            if len(self._seen) >= self._limit:
                return "replay_cache_full"  # fail closed: espellere riaprirebbe un replay dentro la finestra
            # Il nonce resta fino a che il suo timestamp potrebbe ancora essere accettato.
            self._seen[key] = max(now, sent) + self._window
        return None


# ---- validazione dei campi -------------------------------------------------------------------------------------------


class ValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _has_bad_chars(text: str) -> bool:
    return any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 for c in text)


def validate_identifier(value, field_name: str, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ValidationError(f"missing_{field_name}")
        return None
    if not isinstance(value, str) or not _ID_RE.match(value.strip()):
        raise ValidationError(f"invalid_{field_name}")
    return value.strip()


def validate_text(value, field_name: str, *, max_chars: int, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValidationError(f"missing_{field_name}")
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"invalid_{field_name}")
    text = value.strip()
    if required and not text:
        raise ValidationError(f"missing_{field_name}")
    if len(text) > max_chars:
        raise ValidationError(f"{field_name}_too_long")
    if _has_bad_chars(text):
        raise ValidationError(f"invalid_{field_name}")
    return text


def validate_command_body(body: dict) -> dict:
    return {
        "text": validate_text(body.get("text"), "text", max_chars=MAX_TEXT_CHARS, required=True),
        "device_id": validate_identifier(body.get("device_id"), "device_id"),
        "session_id": validate_identifier(body.get("session_id"), "session_id"),
    }


def validate_claim_body(body: dict) -> dict:
    return {"name": validate_text(body.get("name"), "name", max_chars=MAX_NAME_CHARS)}


def check_protocol(header: str | None) -> tuple[bool, dict]:
    """(compatibile, dettagli). Nessun header = client legacy, accettato: un client che non dichiara la sua
    versione non e' un errore, e' un client precedente a questa regola."""
    supported = {"supported_min": MIN_PROTOCOL_VERSION, "supported_max": PROTOCOL_VERSION}
    if header is None:
        return True, supported
    try:
        version = int(header)
    except ValueError:
        return False, supported
    return MIN_PROTOCOL_VERSION <= version <= PROTOCOL_VERSION, supported


# ---- audit ------------------------------------------------------------------------------------------------------------


class CompanionAudit:
    """Registro append-only con catena di hash: ogni riga porta lo SHA-256 della riga precedente."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path) if path is not None else DEFAULT_AUDIT_PATH
        self._clock = clock
        self._lock = threading.Lock()
        self._last_hash: str | None = None

    @staticmethod
    def _digest(prev: str, body: str) -> str:
        return hashlib.sha256((prev + "|" + body).encode("utf-8")).hexdigest()

    def _load_last_hash(self) -> str:
        last = "0" * 64
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        try:
                            last = json.loads(line)["hash"]
                        except (ValueError, KeyError):
                            pass  # riga corrotta: verify() la segnalera'; la catena riparte dall'ultimo hash valido
        return last

    def record(self, event: str, **fields) -> dict:
        with self._lock:
            if self._last_hash is None:
                self._last_hash = self._load_last_hash()
            entry = {"ts": round(self._clock(), 3), "event": event, **fields}
            body = json.dumps(entry, sort_keys=True, ensure_ascii=False)
            entry_hash = self._digest(self._last_hash, body)
            line = json.dumps({"prev": self._last_hash, "hash": entry_hash, "entry": entry}, ensure_ascii=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._last_hash = entry_hash
            return entry

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line)["entry"])
                except (ValueError, KeyError):
                    continue
        return rows

    def head(self) -> str:
        with self._lock:
            if self._last_hash is None:
                self._last_hash = self._load_last_hash()
            return self._last_hash

    def verify(self) -> tuple[bool, int | None]:
        """(integro, numero della prima riga non valida). Rileva una riga modificata, tolta o inserita in mezzo."""
        prev = "0" * 64
        if not self.path.exists():
            return True, None
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                body = json.dumps(row["entry"], sort_keys=True, ensure_ascii=False)
                if row["prev"] != prev or row["hash"] != self._digest(prev, body):
                    return False, number
                prev = row["hash"]
            except (ValueError, KeyError, TypeError):
                return False, number
        return True, None


def text_fingerprint(text: str) -> dict:
    """Cio' che si registra di un comando: lunghezza e hash, mai il testo."""
    return {"text_len": len(text), "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


# ---- il guardiano ------------------------------------------------------------------------------------------------------


@dataclass
class CompanionGuard:
    """Insieme delle difese, con default sicuri. Ogni pezzo e' sostituibile nei test."""

    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    auth_throttle: AuthFailureThrottle = field(default_factory=AuthFailureThrottle)
    replay: ReplayGuard = field(default_factory=ReplayGuard)
    audit: CompanionAudit | None = None
    max_body_bytes: int = MAX_BODY_BYTES
    max_streams_per_identity: int = MAX_STREAMS_PER_IDENTITY
    # None = automatico: obbligatorio se il server e' esposto su un'interfaccia non locale.
    require_replay_protection: bool | None = None
    default_capabilities: frozenset = DEFAULT_CAPABILITIES

    def __post_init__(self) -> None:
        self._capabilities: dict[str, frozenset[EndpointClass]] = {}
        self._streams: dict[str, int] = {}
        self._lock = threading.Lock()

    # capability per dispositivo (in memoria: vedi il docstring del modulo)
    def set_capabilities(self, device_id: str, classes) -> None:
        self._capabilities[device_id] = frozenset(EndpointClass(c) for c in classes)

    def capabilities_of(self, device_id: str | None) -> frozenset:
        if device_id is None:
            return self.default_capabilities
        return self._capabilities.get(device_id, self.default_capabilities)

    def allowed(self, device_id: str | None, cls: EndpointClass) -> bool:
        return cls in self.capabilities_of(device_id)

    def replay_required(self, host: str) -> bool:
        if self.require_replay_protection is not None:
            return self.require_replay_protection
        return not is_loopback_host(host)

    # flussi SSE contemporanei per identita'
    def open_stream(self, identity: str) -> bool:
        with self._lock:
            if self._streams.get(identity, 0) >= self.max_streams_per_identity:
                return False
            self._streams[identity] = self._streams.get(identity, 0) + 1
            return True

    def close_stream(self, identity: str) -> None:
        with self._lock:
            count = self._streams.get(identity, 0) - 1
            if count <= 0:
                self._streams.pop(identity, None)
            else:
                self._streams[identity] = count
