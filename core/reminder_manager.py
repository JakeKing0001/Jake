import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ReminderManager:
    """Gestisce i promemoria di Jake (v1.2, Jake proattivo): li salva e restituisce quelli scaduti.
    Dalla v3.0 ospita anche i timer (kind='timer'): stessa tabella, stesso scheduler, ma
    distinguibili all'annuncio e nei comandi 'annulla il timer'."""

    DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_memory.db"

    def __init__(self, db_path: Path | None = None):
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
                fired INTEGER NOT NULL DEFAULT 0,
                recur_time TEXT,
                kind TEXT NOT NULL DEFAULT 'reminder'
            );
            """
        )
        self._connection.commit()
        # Colonne aggiunte dopo la prima versione dello schema: ALTER TABLE va provato a parte
        # perche' CREATE TABLE IF NOT EXISTS non aggiorna uno schema gia' esistente su disco.
        for statement in (
            "ALTER TABLE reminders ADD COLUMN recur_time TEXT",
            "ALTER TABLE reminders ADD COLUMN kind TEXT NOT NULL DEFAULT 'reminder'",
        ):
            try:
                self._connection.execute(statement)
                self._connection.commit()
            except sqlite3.OperationalError:
                pass  # colonna gia' presente

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def add(self, text: str, due_at: datetime, recur_time: str | None = None, kind: str = "reminder") -> int | None:
        cursor = self._connection.execute(
            "INSERT INTO reminders (text, due_at, created_at, fired, recur_time, kind) VALUES (?, ?, ?, 0, ?, ?)",
            (text, due_at.isoformat(), self._now().isoformat(), recur_time, kind),
        )
        self._connection.commit()
        return cursor.lastrowid

    def due_reminders(self) -> list[dict]:
        """Restituisce i promemoria scaduti non ancora notificati. Quelli ricorrenti (recur_time
        impostato) vengono subito riprogrammati per il giorno successivo invece di essere marcati
        definitivamente 'fired', cosi' continuano a ripresentarsi ogni giorno."""
        now_utc = self._now()
        rows = self._connection.execute(
            "SELECT id, text, due_at, recur_time, kind FROM reminders WHERE fired = 0 AND due_at <= ? ORDER BY due_at ASC",
            (now_utc.isoformat(),),
        ).fetchall()
        for row in rows:
            if row["recur_time"]:
                # +1 giorno alla volta non basta se Jake e' rimasto spento piu' a lungo di un
                # giorno: il nuovo due_at sarebbe ANCORA scaduto, e la prossima chiamata (ogni
                # 20s, vedi core/scheduler.py) lo farebbe scattare di nuovo, e ancora, finche'
                # la data non raggiunge oggi - un promemoria giornaliero perso per 3 giorni
                # suonerebbe 4 volte di fila nel giro di un minuto invece di una sola.
                # Riprodotto per davvero prima della correzione. Si avanza finche' non e' nel
                # futuro, cosi' si recupera in un colpo solo restando comunque notificato una
                # volta sola per questa chiamata.
                next_due = datetime.fromisoformat(row["due_at"]) + timedelta(days=1)
                while next_due <= now_utc:
                    next_due += timedelta(days=1)
                self._connection.execute(
                    "UPDATE reminders SET due_at = ? WHERE id = ?", (next_due.isoformat(), row["id"])
                )
            else:
                self._connection.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (row["id"],))
        if rows:
            self._connection.commit()
        return [dict(row) for row in rows]

    def list_upcoming(self, limit: int = 10, kind: str | None = None) -> list[dict]:
        if kind:
            rows = self._connection.execute(
                "SELECT id, text, due_at, recur_time, kind FROM reminders WHERE fired = 0 AND kind = ? ORDER BY due_at ASC LIMIT ?",
                (kind, limit),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT id, text, due_at, recur_time, kind FROM reminders WHERE fired = 0 AND kind = 'reminder' ORDER BY due_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_matching(self, query: str, kind: str = "reminder") -> dict | None:
        row = self._connection.execute(
            "SELECT id, text, due_at, kind FROM reminders WHERE fired = 0 AND kind = ? AND text LIKE ? ORDER BY due_at ASC LIMIT 1",
            (kind, f"%{query}%"),
        ).fetchone()
        return dict(row) if row else None

    def delete_matching(self, query: str, kind: str = "reminder") -> dict | None:
        found = self.find_matching(query, kind=kind)
        if found is None:
            return None
        self._connection.execute("DELETE FROM reminders WHERE id = ?", (found["id"],))
        self._connection.commit()
        return found

    def snooze_matching(self, query: str, minutes: int) -> dict | None:
        found = self.find_matching(query)
        if found is None:
            return None
        new_due = self._now() + timedelta(minutes=minutes)
        self._connection.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (new_due.isoformat(), found["id"]))
        self._connection.commit()
        found["due_at"] = new_due.isoformat()
        return found

    def close(self) -> None:
        self._connection.close()
