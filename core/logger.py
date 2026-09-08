import json
import logging
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "jake.log"
DEFAULT_ACTION_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_actions.jsonl"

# Senza rotazione jake.log cresce senza limite per tutta la vita dell'installazione: 2 MB per
# file x 4 file (quello attivo + 3 di backup) tengono comunque a disposizione molta cronologia
# per il debug, senza rischiare di riempire il disco su una macchina lasciata accesa a lungo.
MAX_LOG_BYTES = 2_000_000
BACKUP_COUNT = 3


def get_logger(name: str = "jake", log_path: Path | None = None) -> logging.Logger:
    """Restituisce il logger di Jake, configurato una sola volta anche se richiamato più volte."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    path = Path(log_path) if log_path else DEFAULT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        path, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def new_trace_id() -> str:
    """Un id breve per correlare tutte le righe di log prodotte da un'unica richiesta (F0: log
    strutturati con trace_id). 12 esadecimali da uuid4: bastano a non collidere tra le righe di
    un log locale, non sono pensati per identificare qualcosa a lungo termine."""
    return uuid.uuid4().hex[:12]


def get_action_logger(log_path: Path | None = None) -> logging.Logger:
    """Logger separato da jake.log (F0): un file JSON Lines, una riga per azione, pensato per
    essere letto da uno script o una dashboard locale invece che da un umano - vedi log_action()
    per lo schema dei campi. Stessa rotazione di get_logger(), stesso pattern "configurato una
    sola volta"."""
    logger = logging.getLogger("jake.actions")
    if logger.handlers:
        return logger

    path = Path(log_path) if log_path else DEFAULT_ACTION_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        path, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(message)s"))  # ogni riga e' gia' un JSON completo
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_action(
    trace_id: str,
    *,
    private: bool = False,
    duration_ms: float | None = None,
    model: str | None = None,
    skill: str | None = None,
    risk_decision: str | None = None,
    result: str | None = None,
    verified: bool | None = None,
    verification_note: str | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Scrive una riga in jake_actions.jsonl (F0: trace_id, durata, modello, skill, decisione di
    rischio, risultato e prova di verifica). verified=None (il default) significa "non
    verificato", non "verificato con esito ignoto": chi chiama deve passare True/False solo
    quando ha davvero controllato l'effetto (vedi core/execution_safety.py), altrimenti il
    campo resta assente e la riga dichiara esplicitamente il limite invece di inventare una prova.

    In modalita' privata (private=True) non scrive trace_id ne' alcun altro campo: solo che una
    richiesta privata e' passata, stesso principio gia' seguito da JakeCore.answer() per jake.log
    e la memoria (core/jake_core.py) - un trace_id che permettesse comunque di isolare le righe
    di UNA specifica richiesta privata vanificherebbe la promessa "nessuna traccia da nessuna
    parte" anche senza contenuto leggibile."""
    log = logger or get_action_logger()
    if private:
        log.info(json.dumps({"ts": time.time(), "private": True}, ensure_ascii=False))
        return
    record = {
        "ts": time.time(),
        "trace_id": trace_id,
        "duration_ms": duration_ms,
        "model": model,
        "skill": skill,
        "risk_decision": risk_decision,
        "result": result,
        "verified": verified,
        "verification_note": verification_note,
    }
    log.info(json.dumps({k: v for k, v in record.items() if v is not None}, ensure_ascii=False))
