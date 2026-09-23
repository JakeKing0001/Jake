import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_schema import SENSITIVITY_LEVELS, ensure_schema


class MemoryManager:
    """Gestisce la memoria a lungo termine di Jake su SQLite (ricordi, preferenze, cronologia).

    F1.8.2 ("serializzare azioni che toccano lo stesso resource key"): stesso principio gia'
    applicato a `core/reminder_manager.py`/`core/todo_manager.py` - la connessione e'
    `check_same_thread=False` perche' `TriggerScheduler` legge/scrive `WorkflowManager`/
    `TriggerManager` (entrambi backed da questa stessa connessione) da un thread separato dal
    principale, ma disattivare quel controllo NON rende la connessione sicura da usare
    concorrentemente da sola (la documentazione di sqlite3 e' esplicita: la responsabilita' di
    serializzare l'accesso resta di chi chiama). Un `RLock` (non un `Lock` semplice) perche'
    diversi metodi pubblici ne chiamano un altro internamente restando nella stessa sezione
    critica (`related()` chiama `recall()`, `summarize_old_history()` chiama `remember()`,
    `set_preference()`/`get_preference()` chiamano `remember()`/`recall()`): un lock non
    rientrante si bloccherebbe per sempre nello stesso thread."""

    DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_memory.db"
    MAX_HISTORY_ENTRIES = 200
    # Soglia di similarita' coseno oltre la quale due ricordi sono considerati "la stessa cosa
    # detta in un altro modo" (v3.4, fase Memory 2.0), non solo "argomento simile": 0.93 e'
    # deliberatamente alto, per aggiornare "il mio compleanno e' il 5 marzo" -> "il mio
    # compleanno e' il 5 di marzo" nello stesso ricordo, senza fondere due fatti diversi ma
    # correlati (es. "mi piace il caffe'" e "mi piace il te'").
    DEDUP_SIMILARITY_THRESHOLD = 0.93

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else self.DEFAULT_DB_PATH
        self._lock = threading.RLock()
        self._connection, self.migration_report = self._open_validated_connection(self.db_path)

    @staticmethod
    def _open_validated_connection(db_path: Path) -> tuple[sqlite3.Connection, object]:
        """Apre e valida una connessione VERA su `db_path` - stesso identico setup usato da
        `__init__` e da `switch_database()` (F2.7, isolamento memoria per profilo): estratto qui
        cosi' i due punti non possano divergere in silenzio (lo stesso principio "due copie
        parallele" gia' messo in guardia altrove in questo progetto)."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: dalla v3.0 TriggerScheduler legge/scrive workflow_manager e
        # trigger_manager (entrambi backed da questa stessa connessione) da un thread in
        # background - stesso accorgimento gia' usato in ReminderManager per lo stesso motivo.
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        # F5.7.6: senza secure_delete SQLite lascia il contenuto di una riga cancellata nelle pagine libere
        # del file finche' non le riscrive: "cancellato" non sarebbe cancellato. Con questa opzione le pagine
        # liberate vengono azzerate.
        connection.execute("PRAGMA secure_delete = ON")
        # F5.1: schema versionato con migrazioni transazionali e backup prima di toccare dati esistenti.
        try:
            migration_report = ensure_schema(connection, db_path)
        except BaseException:
            # un file piu' nuovo del codice, una migrazione fallita: la connessione non deve restare aperta sul
            # file (su Windows lo terrebbe bloccato: nemmeno un ripristino da backup potrebbe sostituirlo)
            connection.close()
            raise
        return connection, migration_report

    def switch_database(self, db_path: Path) -> None:
        """Ripunta QUESTA STESSA istanza a un altro file SQLite (F2.7, isolamento memoria per
        profilo: cambiare "chi sta parlando" cambia il database attivo, senza dover ricostruire
        `WorkflowManager`/`TriggerManager`/`ProcedureManager`/`ContactBook`/le skill di memoria -
        tutti costruiti con QUESTA identica istanza e tenuti come riferimento fisso dalla loro
        stessa costruzione, mai riletti da `JakeCore` a ogni chiamata; vedi
        ROADMAP_EXECUTION.md F2.7 per l'indagine che ha verificato questo prima di scrivere
        qualunque codice). Sicuro: OGNI metodo pubblico di questa classe gia' passa da
        `self._lock` (un RLock) prima di leggere o scrivere `self._connection` - verificato
        metodo per metodo, non assunto - quindi nessun lettore/scrittore in un altro thread puo'
        mai vedere una connessione a meta' sostituita. La connessione precedente viene chiusa
        SOLO dopo che la nuova e' stata aperta e validata con successo: un file nuovo corrotto o
        con una migrazione fallita non lascia mai questa istanza senza alcuna connessione valida."""
        new_path = Path(db_path)
        with self._lock:
            new_connection, migration_report = self._open_validated_connection(new_path)
            self._connection.close()
            self._connection = new_connection
            self.db_path = new_path
            self.migration_report = migration_report

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def remember(
        self,
        key: str,
        value: str,
        category: str = "fact",
        importance: int = 1,
        embedding: list | None = None,
        project: str | None = None,
        source: str = "user",
        ttl_days: float | None = None,
        sensitivity: str | None = None,
        owner: str | None = None,
        confidence: float | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        created_by: str | None = None,
    ) -> None:
        """Salva o aggiorna un ricordo (upsert su key+category). Se e' fornito un embedding e
        un ricordo esistente nella stessa categoria/progetto e' semanticamente quasi identico
        (v3.4, vedi DEDUP_SIMILARITY_THRESHOLD), aggiorna QUELLO invece di crearne uno nuovo con
        una chiave diversa: altrimenti "il mio compleanno e' il 5 marzo" seguito da "ricordati
        che compio gli anni il 5 di marzo" produrrebbe due ricordi separati per lo stesso fatto,
        che invecchiando in modo indipendente potrebbero anche finire per contraddirsi.

        source (F5, Memory 2.0, provenienza): "user" per default (un comando esplicito
        dell'utente e' la fonte piu' comune), "inferred" per un'ipotesi dedotta da Jake,
        "agent:<nome>" per una decisione autonoma di un agente - vedi core/system_advisor.py per
        il primo chiamante reale con source="inferred". ttl_days (F5, scadenza): giorni da ora
        dopo cui il ricordo smette di comparire in recall()/semantic_recall() (vedi
        include_expired la' sotto) - None (default) significa 'nessuna scadenza', non 'scade
        subito'. Un ttl esplicito e' una decisione presa da CHI SALVA il ricordo (sa gia' che
        quel fatto ha vita breve, es. 'oggi piove'), diverso da purge_history_older_than (una
        policy di retention decisa DOPO, dall'utente, per la privacy).

        Metadati F5.1.3 (tutti opzionali): sensitivity (unknown/public/personal/sensitive/secret), owner,
        created_by, confidence (0-1), valid_from/valid_until (tempo di validita', ISO 8601). Se non dati,
        un ricordo NUOVO nasce con 'unknown' (esplicito, mai NULL) e uno esistente conserva i valori che
        ha: un aggiornamento del testo non azzera la sensibilita' scelta prima. created_by si scrive una
        sola volta (il primo autore resta l'autore)."""
        self._validate_metadata(sensitivity, confidence, valid_from, valid_until)
        with self._lock:
            now = self._now()
            embedding_json = json.dumps(embedding) if embedding else None
            expires_at = (
                (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat() if ttl_days is not None else None
            )

            if embedding:
                duplicate_key = self._find_duplicate_key(embedding, category, project, exclude_key=key)
                if duplicate_key is not None:
                    key = duplicate_key

            existed = self._connection.execute(
                "SELECT 1 FROM memories WHERE key = ? AND category = ?", (key, category),
            ).fetchone() is not None
            self._connection.execute(
                """
                INSERT INTO memories
                    (key, value, category, importance, created_at, updated_at, embedding, project, source, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key, category) DO UPDATE SET
                    value = excluded.value,
                    importance = excluded.importance,
                    updated_at = excluded.updated_at,
                    embedding = excluded.embedding,
                    project = excluded.project,
                    source = excluded.source,
                    expires_at = excluded.expires_at
                """,
                (key, value, category, importance, now, now, embedding_json, project, source, expires_at),
            )
            # chi ha creato un ricordo NUOVO senza dirlo e' la sua fonte (user / inferred / agent:x): la provenienza
            # che gia' si registra. Su un ricordo esistente created_by non cambia (vedi _apply_metadata).
            author = created_by if created_by is not None else (None if existed else source)
            self._apply_metadata(key, category, sensitivity, owner, confidence, valid_from, valid_until, author)
            self._audit(key, category, "updated" if existed else "created", created_by or source)
            self._connection.commit()

    @staticmethod
    def _validate_metadata(sensitivity, confidence, valid_from, valid_until) -> None:
        if sensitivity is not None and sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError(f"sensitivity non valida: {sensitivity!r} (ammesse: {', '.join(SENSITIVITY_LEVELS)})")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence deve essere tra 0 e 1")
        if valid_from and valid_until and valid_from > valid_until:
            raise ValueError("valid_from non puo' essere dopo valid_until")

    def _apply_metadata(self, key, category, sensitivity, owner, confidence, valid_from, valid_until, created_by) -> None:
        """Scrive SOLO i metadati dati; created_by solo se il ricordo non ha ancora un autore."""
        assignments, params = [], []
        for column, value in (("sensitivity", sensitivity), ("owner", owner), ("confidence", confidence),
                              ("valid_from", valid_from), ("valid_until", valid_until)):
            if value is not None:
                assignments.append(f"{column} = ?")
                params.append(value)
        if created_by is not None:
            assignments.append("created_by = CASE WHEN created_by = 'unknown' THEN ? ELSE created_by END")
            params.append(created_by)
        if assignments:
            self._connection.execute(
                f"UPDATE memories SET {', '.join(assignments)} WHERE key = ? AND category = ?", (*params, key, category),
            )

    MAX_AUDIT_EVENTS_PER_MEMORY = 200

    def _audit(self, key: str, category: str, event: str, actor: str = "unknown", detail: str = "") -> None:
        """Registro di cosa e' successo a un ricordo (F5.7.3). Limitato per ricordo: un ricordo letto mille
        volte non deve far crescere il database senza fine."""
        self._connection.execute(
            "INSERT INTO memory_audit(memory_key, memory_category, event, actor, at, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (key, category, event, actor, self._now(), detail),
        )
        self._connection.execute(
            "DELETE FROM memory_audit WHERE memory_key = ? AND memory_category = ? AND id NOT IN ("
            "SELECT id FROM memory_audit WHERE memory_key = ? AND memory_category = ? ORDER BY id DESC LIMIT ?)",
            (key, category, key, category, self.MAX_AUDIT_EVENTS_PER_MEMORY),
        )

    def _find_duplicate_key(self, embedding: list, category: str, project: str | None, exclude_key: str) -> str | None:
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

    _RETURNED_COLUMNS = "key, value, category, importance, updated_at, project, source, expires_at"

    def recall(
        self, key: str | None = None, category: str | None = None, query: str | None = None,
        project: str | None = None, limit: int = 5,
        since: str | None = None, until: str | None = None, include_expired: bool = False,
    ) -> list[dict]:
        """Recupera ricordi per chiave esatta e/o ricerca libera su chiave/valore.

        since/until (v3.4, query temporali): stringhe ISO 8601, confrontate su updated_at (il
        campo che riflette quando il ricordo e' stato detto o corretto l'ultima volta, non solo
        quando e' stato creato la prima volta). Il confronto testuale funziona perche' ISO 8601
        e' ordinabile lessicograficamente.

        include_expired (F5, scadenza): False di default - un ricordo con un ttl_days passato
        (vedi remember()) non deve piu' comparire nelle risposte normali, esattamente come se
        non ci fosse, ma resta sul disco finche' purge_expired() non lo rimuove per davvero
        (permette un audit/recupero, invece di una cancellazione istantanea e silenziosa)."""
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
        if not include_expired:
            clauses.append("(expires_at IS NULL OR expires_at >= ?)")
            params.append(self._now())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT {self._RETURNED_COLUMNS} FROM memories "
                f"{where} ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def semantic_recall(
        self, query_embedding: list, category: str | None = None, project: str | None = None, limit: int = 5,
        include_expired: bool = False,
    ) -> list[dict]:
        """Recupera i ricordi piu' simili semanticamente a un embedding di query.

        Calcola la similarita' coseno in Python: adeguato alla scala di una memoria personale
        (centinaia/migliaia di ricordi), non a un vero indice vettoriale su larga scala.
        include_expired: stesso significato di recall() sopra."""
        from core.embedding_provider import EmbeddingProvider

        clauses = ["embedding IS NOT NULL"]
        params = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if project:
            clauses.append("project = ?")
            params.append(project)
        if not include_expired:
            clauses.append("(expires_at IS NULL OR expires_at >= ?)")
            params.append(self._now())

        with self._lock:
            rows = self._connection.execute(
                f"SELECT {self._RETURNED_COLUMNS}, embedding "
                f"FROM memories WHERE {' AND '.join(clauses)}",
                params,
            ).fetchall()

        scored = []
        for row in rows:
            embedding = json.loads(row["embedding"])
            score = EmbeddingProvider.cosine_similarity(query_embedding, embedding)
            entry = {k: row[k] for k in ("key", "value", "category", "importance", "updated_at", "project", "source", "expires_at")}
            entry["score"] = score
            scored.append(entry)

        scored.sort(key=lambda entry: entry["score"], reverse=True)
        return scored[:limit]

    def purge_expired(self) -> int:
        """Rimuove per davvero i ricordi la cui scadenza (expires_at, vedi remember() ttl_days)
        e' passata. Non automatico ad ogni avvio (nessun chiamante lo invoca da solo oggi):
        recall()/semantic_recall() gia' li nascondono di default, quindi non c'e' fretta di
        cancellarli - questo metodo esiste per una pulizia periodica esplicita (es. un futuro
        hook di manutenzione in core/system_advisor.py), non per essere invocato ad ogni turno."""
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?", (self._now(),)
            )
            self._connection.commit()
            return cursor.rowcount

    def forget(self, key: str, category: str | None = None) -> bool:
        """Elimina i ricordi con la chiave indicata. Restituisce True se qualcosa e' stato rimosso."""
        with self._lock:
            if category:
                cursor = self._connection.execute(
                    "DELETE FROM memories WHERE key = ? AND category = ?", (key, category)
                )
                self._connection.execute(
                    "DELETE FROM memory_relations WHERE (subject_key = ? AND subject_category = ?) "
                    "OR (object_key = ? AND object_category = ?)", (key, category, key, category),
                )
            else:
                cursor = self._connection.execute("DELETE FROM memories WHERE key = ?", (key,))
                # Senza categoria puo' esserci piu' di un ricordo con questa chiave: rimuove i
                # collegamenti di ognuno, per non lasciare archi del grafo che puntano al nulla.
                self._connection.execute(
                    "DELETE FROM memory_relations WHERE subject_key = ? OR object_key = ?", (key, key),
                )
            self._connection.commit()
            return cursor.rowcount > 0

    def count_memories(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) FROM memories").fetchone()
            return row[0] if row else 0

    # ---- grafo di conoscenza personale (v4.4, Personal Knowledge Graph) -------------------
    # Le altre memorie di Jake (ricordi, contatti, todo, automazioni...) restano ognuna nel
    # proprio store separato senza alcun legame tra loro: un ricordo taggato project="NEST" non
    # sa nulla dei contatti o degli altri ricordi collegati a quello stesso progetto. Questa
    # tabella e' un semplice triple store (soggetto, predicato, oggetto) sopra ai ricordi
    # esistenti: non un motore a grafo completo, ma abbastanza per rispondere a "cosa so su X,
    # e cosa e' collegato a X?" seguendo un salto invece di dover ricordare ogni collegamento a
    # mano ogni volta.

    def link(self, subject_key: str, subject_category: str, predicate: str, object_key: str, object_category: str) -> None:
        """Crea una relazione con nome tra due ricordi gia' esistenti (es. 'Mario' -lavora_per-> 'Acme')."""
        with self._lock:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO memory_relations
                    (subject_key, subject_category, predicate, object_key, object_category, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (subject_key, subject_category, predicate, object_key, object_category, self._now()),
            )
            self._connection.commit()

    def unlink(self, subject_key: str, subject_category: str, predicate: str, object_key: str, object_category: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM memory_relations WHERE subject_key = ? AND subject_category = ? AND predicate = ? "
                "AND object_key = ? AND object_category = ?",
                (subject_key, subject_category, predicate, object_key, object_category),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def related(self, key: str, category: str = "fact", predicate: str | None = None) -> list[dict]:
        """Ricordi collegati a (key, category) come soggetto, con il predicato e il valore
        attuale del ricordo collegato. value e' None se l'oggetto non esiste (piu') come
        ricordo: forget() ripulisce sempre gli archi del nodo che cancella, quindi in pratica
        capita solo se un arco e' stato creato verso una chiave mai salvata."""
        clauses = ["subject_key = ?", "subject_category = ?"]
        params = [key, category]
        if predicate:
            clauses.append("predicate = ?")
            params.append(predicate)

        # RLock rientrante: recall() qui sotto riacquisisce lo stesso lock nello stesso thread
        # senza bloccarsi, cosi' l'intera query+arricchimento resta una sola sezione critica.
        with self._lock:
            rows = self._connection.execute(
                f"SELECT predicate, object_key, object_category FROM memory_relations WHERE {' AND '.join(clauses)}"
                " ORDER BY id ASC",
                params,
            ).fetchall()

            related_entries = []
            for row in rows:
                target = self.recall(key=row["object_key"], category=row["object_category"], limit=1)
                related_entries.append({
                    "predicate": row["predicate"],
                    "key": row["object_key"],
                    "category": row["object_category"],
                    "value": target[0]["value"] if target else None,
                })
            return related_entries

    def set_preference(self, name: str, value: str) -> None:
        self.remember(name, value, category="preference")

    def get_preference(self, name: str, default=None):
        results = self.recall(key=name, category="preference", limit=1)
        return results[0]["value"] if results else default

    def log_turn(self, role: str, text: str) -> None:
        """Registra un turno di conversazione nella cronologia a lungo termine."""
        with self._lock:
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
        with self._lock:
            rows = self._connection.execute(
                "SELECT role, text, created_at FROM conversation_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def summarize_old_history(self, summarizer, keep_recent: int = 50) -> bool:
        """Comprime i turni piu' vecchi di keep_recent in un'unica memoria 'summary', poi li elimina.

        Non ha effetto (ritorna False) finche' la cronologia resta sotto la soglia: e' economico
        richiamarlo a ogni turno, il lavoro vero scatta solo occasionalmente."""
        with self._lock:
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

    def purge_history_older_than(self, days: float) -> int:
        """Elimina la cronologia di conversazione (e i suoi riassunti automatici, categoria
        'summary') piu' vecchia di 'days' giorni: la politica di retention (v5.6, Privacy
        Engine). Restituisce quante righe sono state rimosse in totale.

        Deliberatamente SOLO su richiesta esplicita dell'utente (vedi PURGE_OLD_HISTORY in
        skills/privacy.py), mai automatica in background: cancellare dati dell'utente senza che
        li abbia chiesti sarebbe un danno silenzioso, non una funzionalita' di privacy. Non
        tocca le altre categorie di ricordi (fact/preference/...): quelle l'utente le ha chieste
        esplicitamente di ricordare, un limite di tempo automatico le tradirebbe."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            history_cursor = self._connection.execute(
                "DELETE FROM conversation_history WHERE created_at < ?", (cutoff,)
            )
            summary_cursor = self._connection.execute(
                "DELETE FROM memories WHERE category = 'summary' AND created_at < ?", (cutoff,)
            )
            self._connection.commit()
            return history_cursor.rowcount + summary_cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @property
    def lock(self) -> threading.RLock:
        """Il RLock interno, esposto per chi ha bisogno di una sequenza read-modify-write ATOMICA
        su piu' chiamate pubbliche (F1.8.7): `remember()`/`recall()` bloccano gia' ciascuna
        singolarmente, ma non una lettura seguita da una scrittura fatte come due chiamate
        separate - un altro thread puo' infilarsi in mezzo. Buco reale trovato e corretto in
        questa sessione in `TriggerManager.mark_fired()` (vedi il suo docstring): usa questa
        property con `with memory_manager.lock:` per estendere la sezione critica a un'intera
        sequenza. E' un RLock, quindi chiamare `remember()`/`recall()` da dentro quel blocco (che
        riacquisiscono lo stesso lock) e' sicuro."""
        return self._lock

    @property
    def connection(self) -> sqlite3.Connection:
        """La connessione SQLite, per i moduli che operano sull'intero archivio (privacy dashboard,
        backup): chi la usa deve tenere `with memory_manager.lock:` per tutta la sequenza."""
        return self._connection
