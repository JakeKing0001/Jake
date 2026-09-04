import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ReminderManager:
    """Gestisce i promemoria di Jake (v1.2, Jake proattivo): li salva e restituisce quelli scaduti."""

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
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                due_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                fired INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._connection.commit()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def add(self, text: str, due_at: datetime) -> int:
        cursor = self._connection.execute(
            "INSERT INTO reminders (text, due_at, created_at, fired) VALUES (?, ?, ?, 0)",
            (text, due_at.isoformat(), self._now().isoformat()),
        )
        self._connection.commit()
        return cursor.lastrowid

    def due_reminders(self) -> list[dict]:
        """Restituisce (e marca come notificati) i promemoria scaduti non ancora notificati."""
        now = self._now().isoformat()
        rows = self._connection.execute(
            "SELECT id, text, due_at FROM reminders WHERE fired = 0 AND due_at <= ? ORDER BY due_at ASC",
            (now,),
        ).fetchall()
        if rows:
            ids = [row["id"] for row in rows]
            self._connection.executemany(
                "UPDATE reminders SET fired = 1 WHERE id = ?", [(i,) for i in ids]
            )
            self._connection.commit()
        return [dict(row) for row in rows]

    def list_upcoming(self, limit: int = 10) -> list[dict]:
        rows = self._connection.execute(
            "SELECT id, text, due_at FROM reminders WHERE fired = 0 ORDER BY due_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._connection.close()
