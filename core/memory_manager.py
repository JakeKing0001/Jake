import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_schema import SENSITIVITY_LEVELS, ensure_schema, kind_of
from core.secrets_vault import SecretsVault, is_protected



def fold_text(text) -> str:
    """Minuscole e senza accenti: "Caffè" e "caffe" sono la stessa parola per chi cerca e per chi riconosce una chiave."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


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

    def _decrypt_if_needed(self, value: str, sensitivity: str) -> str:
        """Decritta un valore se e' stato cifrato per sensibilita'. Decide il marcatore DPAPI sul valore, non la
        sensibilita' letta: recall() non seleziona la colonna sensitivity, e un ricordo poi declassato resta cifrato."""
        if is_protected(value):
            decrypted = self._vault.unprotect(value)
            # Se la decifratura fallisce, restituiamo il valore originale per non perdere dati
            # In un sistema reale, questo potrebbe indicare un problema con il vault DPAPI
            return decrypted if decrypted is not None else value
        return value

    def reveal(self, value):
        """Valore in chiaro di un campo letto direttamente dal database (cifrato a riposo se sensibile)."""
        return self._decrypt_if_needed(value, "") if isinstance(value, str) else value

    def protect_if_sensitive(self, value, sensitivity):
        """Come remember(): un valore 'sensitive'/'secret' si scrive cifrato, mai due volte."""
        if sensitivity in ("sensitive", "secret") and isinstance(value, str) and not is_protected(value):
            return self._vault.protect(value)
        return value

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else self.DEFAULT_DB_PATH
        self._lock = threading.RLock()
        self._connection, self.migration_report = self._open_validated_connection(self.db_path)
        self._vault = SecretsVault()

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
        # F5.4/F5.5: confronto senza maiuscole ne' accenti anche dentro le query ("caffè" trova "caffe" e viceversa)
        vault = SecretsVault()

        def fold_plain(text):
            # ADR 0004: i valori sensibili sono cifrati a riposo; le ricerche testuali li confrontano in chiaro
            if text is None:
                return None
            if is_protected(text):
                text = vault.unprotect(text) or ""
            return fold_text(text)

        connection.create_function("fold", 1, fold_plain, deterministic=True)
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
    ) -> dict:
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

            # F5.7.1/F5.7.6: campo cifrato per valori di memoria classificati sensibili
            # (ADR 0004): cifriamo il valore se la sensibilita' e' "sensitive" o "secret"
            encrypted_value = None
            if sensitivity in ("sensitive", "secret"):
                encrypted_value = self._vault.protect(value)
                # Per i valori cifrati, memorizziamo il valore cifrato e teniamo il valore
                # in chiaro solo in memoria per il processing immediato
                storage_value = encrypted_value
            else:
                storage_value = value

            expires_at = (
                (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat() if ttl_days is not None else None
            )

            if embedding:
                duplicate_key = self._find_duplicate_key(embedding, category, project, exclude_key=key)
                if duplicate_key is not None:
                    key = duplicate_key
            # F5.4 (consolidamento): "caffe", "Caffè" e "caffè " sono lo STESSO ricordo - prima diventavano tre righe con
            # valori diversi, le risposte li ricevevano tutti e il versionamento (niente sovrascritture silenziose) non
            # scattava mai perche' la chiave sembrava nuova
            key = self._existing_key_for(key.strip(), category)

            # Prepara l'embedding per lo storage
            embedding_json = json.dumps(embedding) if embedding is not None else None

            previous_row = self._connection.execute(
                "SELECT value, source FROM memories WHERE key = ? AND category = ?", (key, category),
            ).fetchone()
            existed = previous_row is not None
            # F5.4.1/F5.4.2 (solo per la conoscenza: fatti e preferenze, non i record JSON di procedure, automazioni,
            # contatti o riassunti che per costruzione si sostituiscono): mai una sovrascrittura silenziosa.
            outcome = {"status": "created" if not existed else "unchanged", "previous": None}
            changed = existed and " ".join(str(previous_row["value"]).lower().split()) != " ".join(value.lower().split())
            if changed and category not in self.KNOWLEDGE_CATEGORIES:
                outcome["status"] = "updated"  # record strutturato: si sostituisce, senza versioni
            if changed and category in self.KNOWLEDGE_CATEGORIES:
                old_value, old_source = previous_row["value"], previous_row["source"]
                outcome["previous"] = old_value
                if old_source == "user" and source != "user":
                    # un'inferenza (di Jake o di un agente) non cancella cio' che l'utente ha detto
                    self._record_version(key, category, value, source, now, "conflict_rejected")
                    self._audit(key, category, "conflict", source, "inferenza in conflitto con un fatto dell'utente")
                    self._connection.commit()
                    return {"status": "conflict", "previous": old_value}
                self._record_version(key, category, old_value, old_source, now, "superseded")
                outcome["status"] = "updated"
            self._connection.execute(
                """
                INSERT INTO memories
                    (key, value, category, importance, created_at, updated_at, embedding, project, source, expires_at, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key, category) DO UPDATE SET
                    value = excluded.value,
                    importance = excluded.importance,
                    updated_at = excluded.updated_at,
                    embedding = excluded.embedding,
                    project = excluded.project,
                    source = excluded.source,
                    expires_at = excluded.expires_at
                """,
                (key, storage_value, category, importance, now, now, embedding_json, project, source, expires_at, kind_of(category)),
            )
            # chi ha creato un ricordo NUOVO senza dirlo e' la sua fonte (user / inferred / agent:x): la provenienza
            # che gia' si registra. Su un ricordo esistente created_by non cambia (vedi _apply_metadata).
            author = created_by if created_by is not None else (None if existed else source)
            self._apply_metadata(key, category, sensitivity, owner, confidence, valid_from, valid_until, author)
            self._audit(key, category, "updated" if existed else "created", created_by or source)
            self._connection.commit()
            return outcome

    KNOWLEDGE_CATEGORIES = frozenset({"fact", "preference"})

    @staticmethod
    def canonical_key(key: str) -> str:
        """L'identita' di una chiave: senza maiuscole, accenti, spazi doppi o punteggiatura ai bordi."""
        return " ".join(fold_text(key).split()).strip(" .,;:!?'\"")

    def _existing_key_for(self, key: str, category: str) -> str:
        """La chiave gia' salvata che rappresenta lo stesso ricordo (stessa categoria), o `key` se non c'e'."""
        if self._connection.execute("SELECT 1 FROM memories WHERE key = ? AND category = ?", (key, category)).fetchone():
            return key
        wanted = self.canonical_key(key)
        for row in self._connection.execute("SELECT key FROM memories WHERE category = ?", (category,)).fetchall():
            if self.canonical_key(row[0]) == wanted:
                return row[0]
        return key

    def consolidate_duplicates(self) -> int:
        """F5.4: unisce i ricordi gia' salvati che sono lo stesso ricordo con chiavi scritte in modo diverso (database
        nati prima della chiave canonica). Resta il piu' recente; per fatti e preferenze gli altri valori diventano
        versioni precedenti (niente sparisce in silenzio) e i collegamenti del grafo passano al superstite. Ritorna
        quante righe sono state unite."""
        merged = 0
        with self._lock:
            rows = self._connection.execute(
                "SELECT key, category, value, source, updated_at FROM memories ORDER BY updated_at DESC").fetchall()
            groups: dict[tuple, list] = {}
            for row in rows:
                groups.setdefault((self.canonical_key(row["key"]), row["category"]), []).append(row)
            now = self._now()
            for (_, category), members in groups.items():
                if len(members) < 2:
                    continue
                survivor = members[0]
                for duplicate in members[1:]:
                    if category in self.KNOWLEDGE_CATEGORIES and " ".join(str(duplicate["value"]).lower().split()) != \
                            " ".join(str(survivor["value"]).lower().split()):
                        self._record_version(survivor["key"], category, duplicate["value"], duplicate["source"], now,
                                             "consolidated")
                    for side in ("subject", "object"):
                        self._connection.execute(
                            f"UPDATE OR IGNORE memory_relations SET {side}_key = ? WHERE {side}_key = ? AND {side}_category = ?",
                            (survivor["key"], duplicate["key"], category))
                        self._connection.execute(
                            f"DELETE FROM memory_relations WHERE {side}_key = ? AND {side}_category = ?",
                            (duplicate["key"], category))
                    self._connection.execute("DELETE FROM memories WHERE key = ? AND category = ?",
                                             (duplicate["key"], category))
                    self._audit(duplicate["key"], category, "consolidated", "jake", f"unito a '{survivor['key']}'")
                    merged += 1
            if merged:
                self._connection.commit()
        return merged

    def _record_version(self, key: str, category: str, value: str, source: str, at: str, reason: str) -> None:
        self._connection.execute(
            "INSERT INTO memory_versions (memory_key, memory_category, value, source, valid_until, recorded_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (key, category, value, source or "unknown", at, at, reason),
        )

    def entry(self, key: str, category: str = "fact") -> dict | None:
        """Il ricordo attuale con i campi che decidono quanto pesa cambiarlo (F5.4.3): importanza e pin."""
        with self._lock:
            key = self._existing_key_for(str(key).strip(), category)
            row = self._connection.execute(
                "SELECT value, importance, pinned, source FROM memories WHERE key = ? AND category = ?", (key, category),
            ).fetchone()
        if row is not None:
            row_dict = dict(row)
            # Per decifrare, abbiamo bisogno della sensibilita' dalla tabella memories
            sensitivity_row = self._connection.execute(
                "SELECT sensitivity FROM memories WHERE key = ? AND category = ?", (key, category)
            ).fetchone()
            sensitivity = sensitivity_row["sensitivity"] if sensitivity_row else "unknown"
            # Decrittiamo il valore se necessario
            row_dict["value"] = self._decrypt_if_needed(row_dict["value"], sensitivity)
            return row_dict
        return None

    def versions(self, key: str, category: str = "fact") -> list[dict]:
        """F5.4.2: le versioni precedenti di un ricordo (e le inferenze rifiutate), dalla piu' recente."""
        with self._lock:
            key = self._existing_key_for(str(key).strip(), category)
            rows = self._connection.execute(
                "SELECT value, source, valid_until, reason FROM memory_versions WHERE memory_key = ? AND memory_category = ? "
                "ORDER BY id DESC", (key, category),
            ).fetchall()
            # Decrittiamo i valori se necessario
            result = []
            for row in rows:
                row_dict = dict(row)
                # Per le versioni, controlliamo la sensibilita' della versione originale
                # Nota: la tabella memory_versions non ha una colonna sensitivity, quindi
                # dobbiamo prendere la sensitivita' dal ricordo corrente
                sensitivity_row = self._connection.execute(
                    "SELECT sensitivity FROM memories WHERE key = ? AND category = ?", (key, category)
                ).fetchone()
                sensitivity = sensitivity_row["sensitivity"] if sensitivity_row else "unknown"
                row_dict["value"] = self._decrypt_if_needed(row_dict["value"], sensitivity)
                result.append(row_dict)
            return result

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
        kind: str | None = None,
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
        if kind:
            # F5.1.2: "entity" | "episode" | "procedure" | "record" (core/memory_schema.MEMORY_KINDS)
            clauses.append("kind = ?")
            params.append(kind)
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
            # Decrittiamo i valori se necessario
            result = []
            for row in rows:
                row_dict = dict(row)
                # Decrittiamo il valore se e' stato cifrato
                sensitivity = row_dict.get("sensitivity", "unknown")
                row_dict["value"] = self._decrypt_if_needed(row_dict["value"], sensitivity)
                result.append(row_dict)
            return result

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
            # Decrittiamo il valore se necessario
            value = row["value"]
            sensitivity = row["sensitivity"] if "sensitivity" in row.keys() else "unknown"
            decrypted_value = self._decrypt_if_needed(value, sensitivity)
            entry = {k: row[k] for k in ("key", "value", "category", "importance", "updated_at", "project", "source", "expires_at")}
            entry["value"] = decrypted_value
            entry["score"] = score
            scored.append(entry)

        scored.sort(key=lambda entry: entry["score"], reverse=True)
        return scored[:limit]

    _STOPWORDS = frozenset({
        "che", "chi", "come", "cosa", "dove", "quando", "quale", "quali", "quanto", "perche", "perché", "sono",
        "della", "delle", "dello", "degli", "nella", "nelle", "questo", "questa", "quello", "quella", "sempre",
        "anche", "ancora", "molto", "tutto", "tutti", "hai", "dimmi", "spiegami", "sai", "puoi", "vorrei",
        "what", "when", "where", "which", "does", "the", "and", "with", "about",
    })

    def relevant_for(self, question: str, query_embedding: list | None = None, limit: int = 4,
                     budget_chars: int = 700, semantic_threshold: float = 0.55) -> list[dict]:
        """F5.5/F5.6: i ricordi pertinenti a una domanda libera, per le risposte NORMALI (non solo RECALL).
        Ranking: frase esatta > parole in comune (pesate per importanza e recenza); la similarita' semantica
        entra solo se le parole non trovano nulla; il primo risultato porta con se' i ricordi collegati (un
        salto nel grafo). Si ferma al budget di caratteri: il contesto del modello non deve esplodere.
        Ogni voce ha `why` (perche' e' stata scelta). Ricordi scaduti mai inclusi."""
        import re

        from core.temporal_parser import find_relative_range

        # F5.5 (retrieval temporale): "ieri", "la settimana scorsa"... restringono al periodo, e non sono parole da cercare
        temporal = find_relative_range(question or "")
        lowered_question = (question or "").lower()
        if temporal is not None:
            lowered_question = lowered_question.replace(temporal[1], " ")
        words = [fold_text(w) for w in re.findall(r"[\w']+", lowered_question)
                 if len(w) >= 4 and w not in self._STOPWORDS]
        candidates: dict[tuple, dict] = {}
        if words:
            clauses = " OR ".join(["fold(key) LIKE ? OR fold(value) LIKE ?"] * len(words))
            params = [p for w in words for p in (f"%{w}%", f"%{w}%")]
            with self._lock:
                rows = self._connection.execute(
                    f"SELECT {self._RETURNED_COLUMNS}, pinned, last_used_at FROM memories WHERE ({clauses}) "
                    "AND (expires_at IS NULL OR expires_at >= ?) ORDER BY updated_at DESC LIMIT 50",
                    (*params, self._now()),
                ).fetchall()
            lowered = fold_text(question or "")
            for row in rows:
                entry = dict(row)
                entry["value"] = self.reveal(entry["value"])
                haystack = fold_text(f"{entry['key']} {entry['value']}")
                hits = sum(1 for w in words if w in haystack)
                exact = fold_text(entry["key"]).strip() in lowered
                base = hits / len(words) + 0.05 * int(entry.get("importance") or 0)
                freshness = self._freshness(entry)
                # F5.4.5: il decadimento pesa sulle parole in comune, mai su una chiave citata esplicitamente
                entry["score"] = (2.0 if exact else 0.0) + base * freshness
                entry["why"] = "chiave citata nella domanda" if exact else f"{hits} parole in comune"
                if freshness < 0.75 and not exact:
                    entry["why"] += ", ma non usato da tempo"
                candidates[(entry["key"], entry["category"])] = entry
        if query_embedding is not None:
            # F5.5 (retrieval ibrido): la similarita' semantica conta SEMPRE, non solo quando le parole non trovano
            # nulla - altrimenti una parola in comune con un ricordo sbagliato nascondeva quello giusto per significato.
            for entry in self.semantic_recall(query_embedding, limit=limit):
                if entry["score"] < semantic_threshold:
                    continue
                similarity = entry["score"]
                key = (entry["key"], entry["category"])
                if key in candidates:
                    candidates[key]["score"] += similarity
                    candidates[key]["why"] += f" e simile per significato ({similarity:.2f})"
                else:
                    entry["score"] = similarity * self._freshness(entry)
                    entry["why"] = f"simile per significato ({similarity:.2f})"
                    candidates[key] = entry
        # Prova reale del 27/09/2026: "cosa e' un processore?" riceveva il riassunto di una conversazione passata perche'
        # conteneva "processore". Domanda di conoscenza generale: nessun ricordo, salvo uno nominato esplicitamente; un
        # riassunto di conversazione solo se la domanda parla di conversazioni passate (core/memory_relevance.py).
        from core.memory_relevance import GENERIC, query_kind, refers_to_conversation

        generic = query_kind(question) == GENERIC
        about_conversation = refers_to_conversation(question)
        # F5.5/F5.7 (eval della memoria, 28/09/2026): un ricordo marcato 'secret' (pin, password) entrava nel contesto
        # automatico di qualunque risposta che ne nominasse le parole - e da li' poteva essere detto a voce o arrivare
        # a HUD/companion. Nel contesto automatico mai: lo legge solo chi lo chiede esplicitamente (RECALL e il
        # dashboard della privacy, con le loro regole).
        secret = self._secret_keys()
        # F5.1.2: gli episodi (riassunti, eventi) solo per domande sul passato; le procedure (automazioni, dimostrazioni:
        # passi strutturati, rumore per una risposta normale) solo se la domanda chiede come si fa o le nomina
        procedural = bool(re.search(r"\b(come (si fa|faccio|posso|si)|procedura|passaggi|automazione|routine)\b",
                                    lowered_question))
        candidates = {k: e for k, e in candidates.items()
                      if (not generic or e["why"].startswith("chiave citata"))
                      and (kind_of(e.get("category")) != "episode" or about_conversation)
                      and (kind_of(e.get("category")) != "procedure" or procedural or e["why"].startswith("chiave citata"))
                      and k not in secret}
        if temporal is not None:
            (since, until), phrase = temporal
            candidates = {k: e for k, e in candidates.items() if since <= str(e.get("updated_at") or "") <= until}
            for entry in candidates.values():
                entry["why"] += f", detto {phrase}"
        ranked = sorted(candidates.values(), key=lambda e: (e["score"], e.get("updated_at") or ""), reverse=True)
        if ranked:
            top = ranked[0]
            for related in self.related(top["key"], top.get("category", "fact")):
                identity = (related["key"], related.get("category", "fact"))
                if related.get("value") is None or identity in candidates or identity in secret:
                    continue
                related = dict(related)
                related["why"] = f"collegato a '{top['key']}' ({related.get('predicate')})"
                related["score"] = top["score"] - 0.5
                ranked.append(related)
        chosen: list[dict] = []
        used = 0
        for entry in ranked[:limit + 2]:
            size = len(str(entry.get("key"))) + len(str(entry.get("value")))
            if len(chosen) >= limit or used + size > budget_chars:
                break
            chosen.append(entry)
            used += size
        # Baseline pre-sperimentazione: quali ricordi sono entrati nel turno, con un id opaco (hash della chiave,
        # mai chiave o valore in chiaro), la categoria e il punteggio
        from core.request_context import note_turn

        note_turn(memory=[{"id": hashlib.sha256(f"{e.get('key')}|{e.get('category', 'fact')}".encode()).hexdigest()[:10],
                           "category": e.get("category", "fact"),
                           "score": round(float(e.get("score") or 0.0), 3)} for e in chosen])
        return chosen

    def _secret_keys(self) -> set[tuple]:
        with self._lock:
            rows = self._connection.execute("SELECT key, category FROM memories WHERE sensitivity = 'secret'").fetchall()
        return {(row[0], row[1]) for row in rows}

    # F5.4.5 (decadimento per categoria): dopo quanti giorni senza uso ne' aggiornamenti un ricordo pesa la meta' nella
    # scelta dei ricordi pertinenti. Solo un peso nel ranking: nessun ricordo viene cancellato o nascosto per questo
    # (la cancellazione resta una scelta dell'utente o una scadenza dichiarata, F6.7.5).
    HALF_LIFE_DAYS = {"preference": 365.0, "fact": 180.0}
    DEFAULT_HALF_LIFE_DAYS = 90.0

    def _freshness(self, entry: dict) -> float:
        """Tra 0.5 e 1: 1 per un ricordo usato o aggiornato di recente, fino a 0.5 per uno dimenticato. I ricordi
        fissati (pinned) non decadono (F5.4.6)."""
        from datetime import datetime, timezone

        if entry.get("pinned"):
            return 1.0
        last = max(str(entry.get("updated_at") or ""), str(entry.get("last_used_at") or ""))
        try:
            then = datetime.fromisoformat(last)
        except ValueError:
            return 1.0
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - then).total_seconds() / 86400)
        half_life = self.HALF_LIFE_DAYS.get(str(entry.get("category") or ""), self.DEFAULT_HALF_LIFE_DAYS)
        return 0.5 + 0.5 * 0.5 ** (age_days / half_life)

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
        """Elimina i ricordi con la chiave indicata. Restituisce True se qualcosa e' stato rimosso.
        F5.4: la chiave vale anche scritta in modo diverso ("dimentica il Caffè" cancella "caffe")."""
        with self._lock:
            wanted = self.canonical_key(key)
            clause, params = ("category = ?", (category,)) if category else ("1 = 1", ())
            keys = sorted({row[0] for row in self._connection.execute(
                f"SELECT key FROM memories WHERE {clause}", params).fetchall() if self.canonical_key(row[0]) == wanted})
            if len(keys) > 1 or (keys and keys[0] != key):
                removed = False
                for match in keys:
                    removed = self._forget_exact(match, category) or removed
                self._connection.commit()
                return removed
            removed = self._forget_exact(key, category)
            self._connection.commit()
            return removed

    def _forget_exact(self, key: str, category: str | None) -> bool:
        """Cancella la chiave esatta (e i suoi collegamenti). Il chiamante tiene il lock e fa il commit."""
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

    def history_between(self, since: str, until: str, topic: str | None = None, role: str | None = None) -> list[dict]:
        """F5.2 (episodica): i turni della cronologia in un periodo [since, until), in ordine, eventualmente su un
        argomento (testo che lo contiene) e di un solo ruolo."""
        clauses, params = ["created_at >= ?", "created_at < ?"], [since, until]
        if topic:
            clauses.append("lower(text) LIKE ?")
            params.append(f"%{topic.lower()}%")
        if role:
            clauses.append("role = ?")
            params.append(role)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT role, text, created_at FROM conversation_history WHERE {' AND '.join(clauses)} ORDER BY id ASC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

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

            # un riassunto lo scrive Jake: non e' "me l'hai detto tu"
            self.remember(f"riassunto conversazione del {self._now()}", summary_text, category="summary",
                          source="conversation")
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
