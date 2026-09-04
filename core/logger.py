import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "jake.log"

# Senza rotazione jake.log cresce senza limite per tutta la vita dell'installazione: 2 MB per
# file x 4 file (quello attivo + 3 di backup) tengono comunque a disposizione molta cronologia
# per il debug, senza rischiare di riempire il disco su una macchina lasciata accesa a lungo.
MAX_LOG_BYTES = 2_000_000
BACKUP_COUNT = 3


def get_logger(name: str = "jake", log_path: Path = None) -> logging.Logger:
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
