import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class TodoManager:
    """Gestisce la lista di cose da fare di Jake: a differenza dei promemoria (SET_REMINDER),
    qui non c'e' un orario, solo un elenco di task da completare quando capita."""

    DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_memory.db"

    def __init__(self, db_path: Path = None):
        self.db_path = Path(db_path) if db_path else self.DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                done_at TEXT
            );
            """
        )
        self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def add(self, text: str) -> int:
        cursor = self._connection.execute(
            "INSERT INTO todos (text, created_at, done) VALUES (?, ?, 0)",
            (text, self._now()),
        )
        self._connection.commit()
        return cursor.lastrowid

    def list_pending(self, limit: int = 20) -> list[dict]:
        rows = self._connection.execute(
            "SELECT id, text FROM todos WHERE done = 0 ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_stale_pending(self, days: float = 3) -> list[dict]:
        """Attivita' ancora aperte create da almeno 'days' giorni (v4.2, Proactive
        Intelligence: usata da SystemAdvisor per notare da solo una todo dimenticata, invece
        di aspettare che l'utente chieda LIST_TODOS). Le piu' vecchie per prime."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._connection.execute(
            "SELECT id, text, created_at FROM todos WHERE done = 0 AND created_at <= ? ORDER BY created_at ASC",
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]

    def complete_matching(self, query: str) -> dict | None:
        """Segna come completato il primo task ancora aperto il cui testo contiene 'query'
        (case-insensitive). Restituisce il task completato, o None se non trovato."""
        row = self._connection.execute(
            "SELECT id, text FROM todos WHERE done = 0 AND text LIKE ? ORDER BY id ASC LIMIT 1",
            (f"%{query}%",),
        ).fetchone()
        if row is None:
            return None
        self._connection.execute(
            "UPDATE todos SET done = 1, done_at = ? WHERE id = ?", (self._now(), row["id"])
        )
        self._connection.commit()
        return dict(row)

    def delete_matching(self, query: str) -> dict | None:
        row = self._connection.execute(
            "SELECT id, text FROM todos WHERE done = 0 AND text LIKE ? ORDER BY id ASC LIMIT 1",
            (f"%{query}%",),
        ).fetchone()
        if row is None:
            return None
        self._connection.execute("DELETE FROM todos WHERE id = ?", (row["id"],))
        self._connection.commit()
        return dict(row)

    def close(self) -> None:
        self._connection.close()
