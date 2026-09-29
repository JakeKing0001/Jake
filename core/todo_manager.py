import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


class TodoManager:
    """Gestisce la lista di cose da fare di Jake: a differenza dei promemoria (SET_REMINDER),
    qui non c'e' un orario, solo un elenco di task da completare quando capita.

    F1.8.2 ("serializzare azioni che toccano lo stesso resource key"): stesso principio di
    core/reminder_manager.py - la connessione e' `check_same_thread=False` perche' `SystemAdvisor`
    (core/system_advisor.py) chiama `list_stale_pending()` da un thread separato, mentre il
    thread principale puo' chiamare `add`/`complete_matching`/`delete_matching` nello stesso
    istante. `complete_matching`/`delete_matching` fanno prima una SELECT poi una UPDATE/DELETE
    sull'id trovato: senza un lock che copra l'intero metodo (non solo le singole query), quella
    finestra e' una race TOCTOU vera, non solo teorica."""

    DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_memory.db"

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else self.DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
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
        # F6.4.5: quante volte Jake ha gia' ricordato una todo, e quando (sopravvive al riavvio). Colonne aggiunte a un
        # database esistente senza toccare le righe.
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(todos)").fetchall()}
        if "nudges" not in columns:
            self._connection.execute("ALTER TABLE todos ADD COLUMN nudges INTEGER NOT NULL DEFAULT 0")
        if "nudged_at" not in columns:
            self._connection.execute("ALTER TABLE todos ADD COLUMN nudged_at TEXT")
        # F6.4.3: un goal e' una todo con dei passi (todo figlie); il prossimo passo e' il primo ancora aperto
        if "parent_id" not in columns:
            self._connection.execute("ALTER TABLE todos ADD COLUMN parent_id INTEGER")
        self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def add(self, text: str, parent_id: int | None = None) -> int | None:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO todos (text, created_at, done, parent_id) VALUES (?, ?, 0, ?)",
                (text, self._now(), parent_id),
            )
            self._connection.commit()
            return cursor.lastrowid

    def list_pending(self, limit: int = 20) -> list[dict]:
        """Le attivita' aperte di primo livello; un goal porta con se' l'avanzamento e il prossimo passo (F6.4.3)."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, text FROM todos WHERE done = 0 AND parent_id IS NULL ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                steps = self._steps(row["id"])
                if steps:
                    item.update(self._progress(steps))
                items.append(item)
            return items

    # ---- F6.4.3: goal, passi e prossimo passo ---------------------------------------------------------------

    def _steps(self, goal_id: int) -> list[dict]:
        return [dict(r) for r in self._connection.execute(
            "SELECT id, text, done FROM todos WHERE parent_id = ? ORDER BY id ASC", (goal_id,)).fetchall()]

    @staticmethod
    def _progress(steps: list[dict]) -> dict:
        pending = [s for s in steps if not s["done"]]
        return {"steps_total": len(steps), "steps_done": len(steps) - len(pending),
                "next_step": pending[0]["text"] if pending else None}

    def _find_goal(self, query: str) -> dict | None:
        row = self._connection.execute(
            "SELECT id, text FROM todos WHERE done = 0 AND parent_id IS NULL AND text LIKE ? ORDER BY id ASC LIMIT 1",
            (f"%{query}%",)).fetchone()
        return dict(row) if row is not None else None

    def plan_goal(self, goal: str, steps: list[str]) -> dict:
        """Il goal (nuovo, o quello aperto con lo stesso testo) con questi passi in fondo alla sua lista."""
        with self._lock:
            found = self._find_goal(goal)
            goal_id = int(found["id"] if found else self.add(goal) or 0)
            for step in steps:
                self.add(step, parent_id=goal_id)
            steps_now = self._steps(goal_id)
            return {"goal": found["text"] if found else goal, **self._progress(steps_now)}

    def next_step(self, goal_query: str) -> dict | None:
        with self._lock:
            found = self._find_goal(goal_query)
            if found is None:
                return None
            steps = self._steps(found["id"])
            return {"goal": found["text"], **self._progress(steps)} if steps else {"goal": found["text"],
                                                                                    "steps_total": 0}

    def list_stale_pending(self, days: float = 3) -> list[dict]:
        """Attivita' ancora aperte create da almeno 'days' giorni (v4.2, Proactive
        Intelligence: usata da SystemAdvisor per notare da solo una todo dimenticata, invece
        di aspettare che l'utente chieda LIST_TODOS). Le piu' vecchie per prime.

        SOLO gli elementi di primo livello (goal) vengono considerati per i promemoria proattivi,
        non i singoli step figli."""
        with self._lock:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            rows = self._connection.execute(
                "SELECT id, text, created_at, nudges, nudged_at FROM todos WHERE done = 0 AND parent_id IS NULL AND created_at <= ? "
                "ORDER BY created_at ASC",
                (cutoff,),
            ).fetchall()
            return [dict(row) for row in rows]

    def record_nudge(self, todo_id: int) -> None:
        """F6.4.5: Jake l'ha appena ricordata all'utente (proattivamente)."""
        with self._lock:
            self._connection.execute("UPDATE todos SET nudges = nudges + 1, nudged_at = ? WHERE id = ?",
                                     (self._now(), todo_id))
            self._connection.commit()

    def complete_matching(self, query: str) -> dict | None:
        """Segna come completato il primo task ancora aperto il cui testo contiene 'query'
        (case-insensitive). Restituisce il task completato, o None se non trovato.

        Se il task e' un goal, completa anche tutti i suoi passi figli nella stessa transazione."""
        with self._lock:
            row = self._connection.execute(
                "SELECT id, text, parent_id FROM todos WHERE done = 0 AND text LIKE ? ORDER BY id ASC LIMIT 1",
                (f"%{query}%",),
            ).fetchone()
            if row is None:
                return None

            todo_id = row["id"]
            parent_id = row["parent_id"]
            self._connection.execute(
                "UPDATE todos SET done = 1, done_at = ? WHERE id = ?", (self._now(), todo_id)
            )

            # Se stiamo completando un goal, completa anche tutti i suoi passi figli
            if parent_id is None:  # Questo e' un goal (non ha parent)
                self._connection.execute(
                    "UPDATE todos SET done = 1, done_at = ? WHERE parent_id = ? AND done = 0",
                    (self._now(), todo_id)
                )

            self._connection.commit()
            completed = dict(row)

            # F6.4.3/F6.4.4: l'ultimo passo di un goal non chiude il goal da solo (fatto e' cio' che l'utente dice):
            # chi risponde lo propone
            if parent_id is not None:  # Questo e' un passo, controlla il goal padre
                goal = self._connection.execute("SELECT text, done FROM todos WHERE id = ?", (parent_id,)).fetchone()
                steps = self._steps(parent_id)
                completed["goal"] = goal["text"] if goal else None
                completed.update(self._progress(steps))
            elif parent_id is None:  # Questo e' un goal, mostra i suoi passi
                steps = self._steps(todo_id)
                completed.update(self._progress(steps))

            return completed

    def delete_matching(self, query: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id, text, parent_id FROM todos WHERE done = 0 AND text LIKE ? ORDER BY id ASC LIMIT 1",
                (f"%{query}%",),
            ).fetchone()
            if row is None:
                return None

            todo_id = row["id"]
            parent_id = row["parent_id"]

            # Se stiamo eliminando un goal, elimina anche tutti i suoi passi figli
            if parent_id is None:  # Questo e' un goal (non ha parent)
                self._connection.execute("DELETE FROM todos WHERE parent_id = ?", (todo_id,))

            self._connection.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            self._connection.commit()
            return dict(row)

    def close(self) -> None:
        with self._lock:
            self._connection.close()