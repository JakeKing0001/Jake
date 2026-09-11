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
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_sessions.jsonl"
MAX_LOG_BYTES = 2_000_000
BACKUP_COUNT = 3


def _redact(value):
    if isinstance(value, str):
        return f"<str:{len(value)} caratteri>"
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
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
            "parameters": dict(parameters or {}) if self.verbatim else _redact(dict(parameters or {})),
            "verbatim": self.verbatim,
            "error": error,
            "risk_decision": risk_decision,
        }
        self._logger.info(json.dumps(record, ensure_ascii=False))
