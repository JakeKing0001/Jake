import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Percorsi standard del file "History" (sqlite) di Chrome/Edge su Windows: entrambi usano lo
# stesso schema Chromium, quindi la stessa query funziona per tutti e due.
_CANDIDATE_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Default" / "History",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data" / "Default" / "History",
]

# Chrome/Edge salvano i timestamp come microsecondi dal 1601-01-01 (epoca WebKit/FILETIME),
# non dal 1970-01-01 (epoca Unix): serve un offset per convertirli in datetime normali.
_WEBKIT_EPOCH = datetime(1601, 1, 1)


def _webkit_to_datetime(webkit_timestamp: int) -> datetime:
    return _WEBKIT_EPOCH + timedelta(microseconds=webkit_timestamp)


def _find_history_file() -> Path | None:
    existing = [path for path in _CANDIDATE_PATHS if path.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def read_recent_history(limit: int = 10) -> list[dict] | None:
    """Legge i siti visitati piu' di recente da Chrome/Edge. Restituisce None se nessuno dei
    due e' installato o se la lettura fallisce (es. permessi).

    Il browser tiene il file History aperto/bloccato mentre gira: per questo lo si copia
    prima in un file temporaneo e si legge la copia, invece di aprire l'originale in place."""
    source = _find_history_file()
    if source is None:
        return None

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        shutil.copyfile(source, tmp_path)

        connection = sqlite3.connect(tmp_path)
        try:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT title, url, last_visit_time FROM urls ORDER BY last_visit_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return [
        {
            "title": row["title"] or row["url"],
            "url": row["url"],
            "visited_at": _webkit_to_datetime(row["last_visit_time"]).isoformat() if row["last_visit_time"] else None,
        }
        for row in rows
    ]
