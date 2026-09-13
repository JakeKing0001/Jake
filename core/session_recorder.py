"""Replay anonimizzato/deterministico delle sessioni fallite (F0, vedi la fase F0 in
ROADMAP.md). Diverso da core/logger.log_action (che scrive OGNI azione, senza parametri, per
capire il comportamento nel tempo): questo modulo scrive SOLO le azioni fallite, CON i
parametri, perche' senza i parametri veri non si puo' far succedere di nuovo lo stesso errore
per verificare un fix - vedi tools/replay_session.py.

Due modalita', entrambe disattivate per default (config `session_recording_enabled`):
- redatta (default quando attivo): i valori stringa dei parametri diventano segnaposto
  "<str:N caratteri>" invece del testo vero - abbastanza per capire la FORMA del fallimento
  (quanti parametri, quali mancano, che tipo hanno) senza scrivere su disco un nome di file, un
  contatto o un appunto veri.
- verbatim (config `session_recording_verbatim`, richiede anche `session_recording_enabled`):
  i parametri restano quelli veri, per poter far RIPARTIRE davvero l'azione fallita e vedere se
  un fix la risolve. E' una scelta esplicita e locale (chi la attiva sa che sta scrivendo dati
  veri su disco): non e' mai il default, e non e' mai attiva in modalita' privata, come ogni
  altra forma di logging in questo progetto (vedi core/jake_core.py, core/logger.py)."""
import json
import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_sessions.jsonl"
MAX_LOG_BYTES = 2_000_000
BACKUP_COUNT = 3

# F1.7.4 ("redazione strutturata per tipo di dato, non solo lunghezza stringa"): prima di questi
# pattern, un percorso, un URL o un indirizzo email diventavano tutti lo stesso "<str:N
# caratteri>" - abbastanza per contare i caratteri, non per capire CHE FORMA aveva il valore
# senza riaprire il file verbatim. Deliberatamente conservativo: quando un valore non corrisponde
# chiaramente a uno di questi tre pattern, resta il segnaposto generico di prima - un falso
# negativo (un percorso non riconosciuto) e' innocuo, un falso positivo rischierebbe di far
# sembrare "sicura" una stringa che in realta' e' testo libero (un appunto, un messaggio).
_EMAIL_RE = re.compile(r"^[^\s@]+@([^\s@]+\.[^\s@]+)$")
_URL_RE = re.compile(r"^(?:https?://|www\.)([^/\s]+)", re.IGNORECASE)
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_UNC_RE = re.compile(r"^\\\\")
_EXTENSION_RE = re.compile(r"\.([A-Za-z0-9]{1,6})$")

# F1.7.4 (terza fetta, "altri tipi di dato per contenuto"): stesso principio conservativo di
# sopra, applicato a indirizzo IP e numero di telefono - il terzo tipo gia' citato nel gap,
# "identificatore di dispositivo", e' rimasto deliberatamente FUORI: a differenza di IP/telefono
# non ha un formato standard riconoscibile (un ID Home Assistant puo' essere un UUID, un hex
# arbitrario, o "dominio.oggetto" - troppo ambiguo per un'euristica per contenuto senza rischiare
# falsi positivi su testo libero qualsiasi).
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
_PHONE_RE = re.compile(r"^\+?[\d\s\-()]{6,20}$")
_PHONE_COUNTRY_CODE_RE = re.compile(r"^\+(\d{1,3})")

# F1.7.4 ("classificazione per nome del parametro, non solo per contenuto"): il resto di questo
# modulo classifica per FORMA del valore, ma un valore sensibile non ha sempre una forma
# riconoscibile - CHECK_PASSWORD_STRENGTH (skills/security_utils.py) prende un parametro
# "password" che di solito non somiglia a un percorso/URL/email, quindi finiva nel segnaposto
# generico "<str:N caratteri>": innocuo per un appunto, ma per una password la LUNGHEZZA ESATTA e'
# gia' un'informazione che non dovrebbe uscire su disco (restringe lo spazio di ricerca di un
# eventuale attacco a forza bruta). Peggio ancora se il valore non e' una stringa (es. un PIN
# numerico): redact_value() lasciava passare bool/int/float invariati, scrivendo il valore VERO in
# chiaro. Controllo per SOSTANTIVO del nome del parametro (non una singola chiave esatta come
# core/config.py::SECRET_KEYS, che riguarda le credenziali di Config, non i parametri di una
# skill): un elenco esplicito e deliberatamente enumerato, coerente con lo stile conservativo del
# resto del modulo - un nome nuovo non ancora previsto qui semplicemente non viene protetto
# (falso negativo, come per i pattern di contenuto sopra), non il contrario.
_SENSITIVE_PARAMETER_NAME_FRAGMENTS = (
    "password", "passphrase", "secret", "token", "pin", "otp", "api_key", "apikey", "credential",
)
_SENSITIVE_PARAMETER_PLACEHOLDER = "<redatto: parametro sensibile per nome, valore mai scritto>"


def _is_sensitive_parameter_name(key: str) -> bool:
    normalized = key.lower()
    return any(fragment in normalized for fragment in _SENSITIVE_PARAMETER_NAME_FRAGMENTS)


def _looks_like_path(value: str) -> bool:
    # Un backslash e' un segnale forte da solo (raro in una frase scritta a mano); "/" da solo
    # e' troppo ambiguo (date, frazioni, "10/09/2026") - richiede anche un'estensione file
    # riconoscibile alla fine per contare come percorso.
    if _WINDOWS_DRIVE_RE.match(value) or _UNC_RE.match(value) or "\\" in value:
        return True
    return "/" in value and bool(_EXTENSION_RE.search(value))


def _looks_like_ipv4(value: str) -> bool:
    match = _IPV4_RE.match(value)
    if not match:
        return False
    # Rifiuta "999.999.999.999": quattro gruppi di cifre separati da "." non bastano da soli, ogni
    # ottetto deve stare nel range valido di un indirizzo IPv4 vero.
    return all(0 <= int(octet) <= 255 for octet in match.groups())


def _looks_like_ipv6(value: str) -> bool:
    # Convalida la struttura (non solo "solo cifre esadecimali e due punti"): un orario scritto
    # come "14:30:00" e' fatto anche lui di sole cifre/":" ma non e' un IPv6 valido - un indirizzo
    # non compresso ha SEMPRE esattamente 8 gruppi, uno compresso con "::" ne ha meno di 8 (la
    # "::" ne rappresenta uno o piu' a zero) e mai piu' di una "::" nello stesso indirizzo.
    if value.count("::") > 1:
        return False
    if "::" in value:
        head, _, tail = value.partition("::")
        groups = [g for g in head.split(":") if g] + [g for g in tail.split(":") if g]
        if len(groups) >= 8:
            return False
    else:
        groups = value.split(":")
        if len(groups) != 8:
            return False
    return bool(groups) and all(re.fullmatch(r"[0-9a-fA-F]{1,4}", g) for g in groups)


def _looks_like_phone_number(value: str) -> bool:
    # Una sequenza di sole cifre e' troppo ambigua (PIN, codice OTP, ID) per essere classificata
    # come telefono solo per la lunghezza: richiede un prefisso "+" o un separatore di
    # formattazione tipico (spazio/trattino/parentesi), oltre a un numero di cifre plausibile per
    # un numero reale (7-15, lo standard internazionale E.164).
    if not _PHONE_RE.match(value):
        return False
    if not (value.startswith("+") or any(ch in value for ch in " -()")):
        return False
    digits = re.sub(r"\D", "", value)
    return 7 <= len(digits) <= 15


def redact_value(value):
    """Sostituisce ogni stringa con un segnaposto che rivela la FORMA del valore senza il
    contenuto vero (ricorsivamente dentro dict/list). Il segnaposto generico "<str:N caratteri>"
    resta il default; email/URL/percorsi/IP/telefoni riconosciuti diventano rispettivamente
    "<email:N caratteri, dominio=...>"/"<url:N caratteri, dominio=...>"/"<path:N caratteri,
    estensione=...>"/"<ip:N caratteri, versione=v4|v6>"/"<telefono:N caratteri[, prefisso=+..]>" -
    il dominio, l'estensione, la versione IP o il prefisso internazionale soli raramente
    identificano una persona, ma aiutano a capire il fallimento (es. "il bug capita solo con i
    PDF", o solo con indirizzi IPv6) senza scrivere su disco l'indirizzo, l'URL completo (che puo'
    contenere un token in query string), il percorso o il numero di telefono veri.
    Pubblica (non piu' `_redact`) perche' anche `tools/diagnostic_bundle.py` (F1.7.7) ha bisogno
    della stessa identica redazione per i parametri che finiscono in un bundle diagnostico - una
    sola funzione, non una seconda copia con una convenzione leggermente diversa.

    F1.7.4: dentro un dict, un valore il cui nome di CHIAVE e' riconosciuto come sensibile (vedi
    `_SENSITIVE_PARAMETER_NAME_FRAGMENTS`) diventa sempre `_SENSITIVE_PARAMETER_PLACEHOLDER`,
    qualunque sia il suo tipo o la sua forma - nessuna lunghezza, nessun dominio/estensione,
    nessun valore vero, nemmeno per un bool/int/float che altrimenti passerebbe INVARIATO (vedi
    sotto)."""
    if isinstance(value, str):
        length = len(value)
        email_match = _EMAIL_RE.match(value)
        if email_match:
            return f"<email:{length} caratteri, dominio={email_match.group(1).lower()}>"
        url_match = _URL_RE.match(value)
        if url_match:
            return f"<url:{length} caratteri, dominio={url_match.group(1).lower()}>"
        if _looks_like_ipv4(value):
            return f"<ip:{length} caratteri, versione=v4>"
        if _looks_like_ipv6(value):
            return f"<ip:{length} caratteri, versione=v6>"
        if _looks_like_phone_number(value):
            country_match = _PHONE_COUNTRY_CODE_RE.match(value)
            if country_match:
                return f"<telefono:{length} caratteri, prefisso=+{country_match.group(1)}>"
            return f"<telefono:{length} caratteri>"
        if _looks_like_path(value):
            ext_match = _EXTENSION_RE.search(value)
            if ext_match:
                return f"<path:{length} caratteri, estensione=.{ext_match.group(1).lower()}>"
            return f"<path:{length} caratteri>"
        return f"<str:{length} caratteri>"
    if isinstance(value, dict):
        return {
            key: _SENSITIVE_PARAMETER_PLACEHOLDER if _is_sensitive_parameter_name(key) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value  # bool/int/float/None: raramente identificano una persona da soli


def _get_file_logger(path: Path) -> logging.Logger:
    """Un logger per file (non condiviso con core/logger.get_logger/get_action_logger, che
    scrivono altrove): stesso schema "un handler alla volta" di quelli, cosi' due
    SessionRecorder puntati a percorsi diversi (es. nei test) non si accavallano."""
    name = f"jake.sessions.{path}"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


class SessionRecorder:
    def __init__(self, enabled: bool = False, verbatim: bool = False, path: Path | None = None):
        self.enabled = enabled
        self.verbatim = verbatim and enabled
        self._path = Path(path) if path else DEFAULT_PATH
        self._logger: logging.Logger | None = None

    def record_failure(
        self, trace_id: str, *, intent: str, parameters: dict, error: str, risk_decision: str, private: bool = False,
    ) -> None:
        """Chiamare solo quando un'azione e' fallita (result inizia con "error:" o simile) - vedi
        i tre punti gia' collegati a log_action (JakeCore._execute_command, TaskAgent._log_step,
        PlanExecutor._log_step), che passano qui lo stesso trace_id per correlare i due log."""
        if not self.enabled or private:
            return
        if self._logger is None:
            self._logger = _get_file_logger(self._path)
        record = {
            "ts": time.time(),
            "trace_id": trace_id,
            "intent": intent,
            "parameters": dict(parameters or {}) if self.verbatim else redact_value(dict(parameters or {})),
            "verbatim": self.verbatim,
            "error": error,
            "risk_decision": risk_decision,
        }
        self._logger.info(json.dumps(record, ensure_ascii=False))
