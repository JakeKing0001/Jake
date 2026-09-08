import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class MemoryManager:
    """Gestisce la memoria a lungo termine di Jake su SQLite (ricordi, preferenze, cronologia)."""

    DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_memory.db"
    MAX_HISTORY_ENTRIES = 200
    # Soglia di similarita' coseno oltre la quale due ricordi sono considerati "la stessa cosa
    # detta in un altro modo" (v3.4, fase Memory 2.0), non solo "argomento simile": 0.93 e'
    # deliberatamente alto, per aggiornare "il mio compleanno e' il 5 marzo" -> "il mio
    # compleanno e' il 5 di marzo" nello stesso ricordo, senza fondere due fatti diversi ma
    # correlati (es. "mi piace il caffe'" e "mi piace il te'").
    DEDUP_SIMILARITY_THRESHOLD = 0.93

    def __init__(self, db_path: Path = None):
        self.db_path = Path(db_path) if db_path else self.DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: dalla v3.0 TriggerScheduler legge/scrive workflow_manager e
        # trigger_manager (entrambi backed da questa stessa connessione) da un thread in
        # background - stesso accorgimento gia' usato in ReminderManager per lo stesso motivo.
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate_schema()

    def _init_schema(self):
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'fact',
                importance INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(key, category)
            );

            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def _migrate_schema(self):
        """Aggiunge colonne introdotte dopo la v0.2 ai database creati con lo schema precedente."""
        existing_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "embedding" not in existing_columns:
            self._connection.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
        if "project" not in existing_columns:
            self._connection.execute("ALTER TABLE memories ADD COLUMN project TEXT")
        self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def remember(
        self,
        key: str,
        value: str,
        category: str = "fact",
        importance: int = 1,
        embedding: list = None,
        project: str = None,
    ) -> None:
        """Salva o aggiorna un ricordo (upsert su key+category). Se e' fornito un embedding e
        un ricordo esistente nella stessa categoria/progetto e' semanticamente quasi identico
        (v3.4, vedi DEDUP_SIMILARITY_THRESHOLD), aggiorna QUELLO invece di crearne uno nuovo con
        una chiave diversa: altrimenti "il mio compleanno e' il 5 marzo" seguito da "ricordati
        che compio gli anni il 5 di marzo" produrrebbe due ricordi separati per lo stesso fatto,
        che invecchiando in modo indipendente potrebbero anche finire per contraddirsi."""
        now = self._now()
        embedding_json = json.dumps(embedding) if embedding else None

        if embedding:
            duplicate_key = self._find_duplicate_key(embedding, category, project, exclude_key=key)
            if duplicate_key is not None:
                key = duplicate_key

        self._connection.execute(
            """
            INSERT INTO memories (key, value, category, importance, created_at, updated_at, embedding, project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key, category) DO UPDATE SET
                value = excluded.value,
                importance = excluded.importance,
                updated_at = excluded.updated_at,
                embedding = excluded.embedding,
                project = excluded.project
            """,
            (key, value, category, importance, now, now, embedding_json, project),
        )
        self._connection.commit()

    def _find_duplicate_key(self, embedding: list, category: str, project: str, exclude_key: str) -> str | None:
        """Chiave del ricordo esistente piu' simile semanticamente a embedding, nella stessa
        categoria/progetto, se supera DEDUP_SIMILARITY_THRESHOLD. None se non c'e' nulla di
        abbastanza simile (compreso il caso, normale, in cui exclude_key e' gia' quello giusto:
        un upsert su una chiave identica non ha bisogno del dedup semantico)."""
        from core.embedding_provider import EmbeddingProvider

        clauses = ["embedding IS NOT NULL", "key != ?"]
        params = [exclude_key]
        if category:
            clauses.append("category = ?")
            params.append(category)
        if project:
            clauses.append("project = ?")
            params.append(project)

        rows = self._connection.execute(
            f"SELECT key, embedding FROM memories WHERE {' AND '.join(clauses)}", params,
        ).fetchall()

        best_key, best_score = None, 0.0
        for row in rows:
            score = EmbeddingProvider.cosine_similarity(embedding, json.loads(row["embedding"]))
            if score > best_score:
                best_key, best_score = row["key"], score
        return best_key if best_score >= self.DEDUP_SIMILARITY_THRESHOLD else None

    def recall(
        self, key: str = None, category: str = None, query: str = None, project: str = None, limit: int = 5,
        since: str = None, until: str = None,
    ) -> list[dict]:
        """Recupera ricordi per chiave esatta e/o ricerca libera su chiave/valore.

        since/until (v3.4, query temporali): stringhe ISO 8601, confrontate su updated_at (il
        campo che riflette quando il ricordo e' stato detto o corretto l'ultima volta, non solo
        quando e' stato creato la prima volta). Il confronto testuale funziona perche' ISO 8601
        e' ordinabile lessicograficamente."""
        clauses = []
        params = []
        if key:
            clauses.append("key = ?")
            params.append(key)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if project:
            clauses.append("project = ?")
            params.append(project)
        if query:
            clauses.append("(key LIKE ? OR value LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if since:
            clauses.append("updated_at >= ?")
            params.append(since)
        if until:
            clauses.append("updated_at <= ?")
            params.append(until)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT key, value, category, importance, updated_at, project FROM memories "
            f"{where} ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def semantic_recall(self, query_embedding: list, category: str = None, project: str = None, limit: int = 5) -> list[dict]:
        """Recupera i ricordi piu' simili semanticamente a un embedding di query.

        Calcola la similarita' coseno in Python: adeguato alla scala di una memoria personale
        (centinaia/migliaia di ricordi), non a un vero indice vettoriale su larga scala."""
        from core.embedding_provider import EmbeddingProvider

        clauses = ["embedding IS NOT NULL"]
        params = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if project:
            clauses.append("project = ?")
            params.append(project)

        rows = self._connection.execute(
            f"SELECT key, value, category, importance, updated_at, project, embedding "
            f"FROM memories WHERE {' AND '.join(clauses)}",
            params,
        ).fetchall()

        scored = []
        for row in rows:
            embedding = json.loads(row["embedding"])
            score = EmbeddingProvider.cosine_similarity(query_embedding, embedding)
            entry = {k: row[k] for k in ("key", "value", "category", "importance", "updated_at", "project")}
            entry["score"] = score
            scored.append(entry)

        scored.sort(key=lambda entry: entry["score"], reverse=True)
        return scored[:limit]

    def forget(self, key: str, category: str = None) -> bool:
        """Elimina i ricordi con la chiave indicata. Restituisce True se qualcosa e' stato rimosso."""
        if category:
            cursor = self._connection.execute(
                "DELETE FROM memories WHERE key = ? AND category = ?", (key, category)
            )
        else:
            cursor = self._connection.execute("DELETE FROM memories WHERE key = ?", (key,))
        self._connection.commit()
        return cursor.rowcount > 0

    def count_memories(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0

    def set_preference(self, name: str, value: str) -> None:
        self.remember(name, value, category="preference")

    def get_preference(self, name: str, default=None):
        results = self.recall(key=name, category="preference", limit=1)
        return results[0]["value"] if results else default

    def log_turn(self, role: str, text: str) -> None:
        """Registra un turno di conversazione nella cronologia a lungo termine."""
        self._connection.execute(
            "INSERT INTO conversation_history (role, text, created_at) VALUES (?, ?, ?)",
            (role, text, self._now()),
        )
        self._connection.execute(
            """
            DELETE FROM conversation_history WHERE id NOT IN (
                SELECT id FROM conversation_history ORDER BY id DESC LIMIT ?
            )
            """,
            (self.MAX_HISTORY_ENTRIES,),
        )
        self._connection.commit()

    def get_recent_history(self, limit: int = 10) -> list[dict]:
        """Restituisce gli ultimi turni di conversazione in ordine cronologico."""
        rows = self._connection.execute(
            "SELECT role, text, created_at FROM conversation_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def summarize_old_history(self, summarizer, keep_recent: int = 50) -> bool:
        """Comprime i turni piu' vecchi di keep_recent in un'unica memoria 'summary', poi li elimina.

        Non ha effetto (ritorna False) finche' la cronologia resta sotto la soglia: e' economico
        richiamarlo a ogni turno, il lavoro vero scatta solo occasionalmente."""
        rows = self._connection.execute(
            "SELECT id, role, text, created_at FROM conversation_history ORDER BY id ASC"
        ).fetchall()
        if len(rows) <= keep_recent:
            return False

        overflow = rows[: len(rows) - keep_recent]
        summary_text = summarizer.summarize([dict(row) for row in overflow])
        if not summary_text:
            return False

        self.remember(f"riassunto conversazione del {self._now()}", summary_text, category="summary")
        self._connection.executemany(
            "DELETE FROM conversation_history WHERE id = ?", [(row["id"],) for row in overflow]
        )
        self._connection.commit()
        return True

    def close(self) -> None:
        self._connection.close()
