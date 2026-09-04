import logging
from pathlib import Path

DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "jake.log"


def get_logger(name: str = "jake", log_path: Path = None) -> logging.Logger:
    """Restituisce il logger di Jake, configurato una sola volta anche se richiamato più volte."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    path = Path(log_path) if log_path else DEFAULT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
