"""Privacy dashboard e portabilita' della memoria (F5.7.1-F5.7.4, F5.7.6, F5.7.7).

Il principio e' che la memoria di Jake e' dell'utente: deve poter trovare un ricordo, capire perche' c'e'
(chi l'ha creato, da dove viene, quanto e' sensibile, quante volte e' stato usato), correggerlo, farlo
scadere, scollegarlo e - soprattutto - cancellarlo DAVVERO, da tutti i posti in cui esiste, con una
ricevuta che lo dimostra.

"Davvero" ha tre parti, ciascuna con un test:
1. `PRAGMA secure_delete` (attivato da `MemoryManager`): SQLite altrimenti lascia il contenuto di una
   riga cancellata nelle pagine libere del file; qui il test cerca i byte del valore nel file dopo la
   cancellazione;
2. gli indici DERIVATI: l'embedding sta nella riga (sparisce con lei), ma esistono indici fuori dal
   database (l'indice degli esempi imparati, NEST...). Si registrano con `register_index` e la ricevuta
   dice per ciascuno se il ricordo e' ancora presente dopo la cancellazione;
3. le COPIE: `purge_everything` toglie anche i backup locali; quelli che l'utente ha esportato altrove
   non si possono raggiungere, e la ricevuta lo dice invece di far credere il contrario.

La ricevuta non contiene il ricordo: solo un hash della chiave, conteggi e verifiche."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_manager import MemoryManager
from core.memory_schema import SENSITIVITY_LEVELS, SCHEMA_VERSION, backups_dir_for

CONFIRM_PHRASE = "ELIMINA TUTTO"
_RECORD_COLUMNS = (
    "id, key, value, category, importance, created_at, updated_at, project, source, expires_at, sensitivity, owner, "
    "created_by, confidence, valid_from, valid_until, pinned, last_used_at, use_count, embedding IS NOT NULL AS has_embedding"
)


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    key: str
    value: str
    category: str
    importance: int
    created_at: str
    updated_at: str
    project: str | None
    source: str
    expires_at: str | None
    sensitivity: str
    owner: str
    created_by: str
    confidence: float | None
    valid_from: str | None
    valid_until: str | None
    pinned: bool
    last_used_at: str | None
    use_count: int
    has_embedding: bool
    expired: bool = False
    why: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class DeletionReceipt:
    memory_hash: str  # sha256(categoria + "\0" + chiave): prova senza rivelare il ricordo
    deleted_at: str
    actor: str
    rows_removed: dict[str, int]
    derived_indexes: dict[str, dict]
    residue_check: str  # "clean" | "not_checked"
    verified: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _hash_memory(key: str, category: str) -> str:
    return hashlib.sha256(f"{category}\0{key}".encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryPrivacyDashboard:
    def __init__(self, memory: MemoryManager, receipts_path: Path | None = None) -> None:
        self.memory = memory
        self.receipts_path = Path(receipts_path) if receipts_path else None
        self._indexes: dict[str, tuple[Callable[[str, str], None], Callable[[str, str], bool], Callable[[], None] | None]] = {}
        self.receipts: list[DeletionReceipt] = []

    # ---- F5.7.1 cercare, filtrare, spiegare -----------------------------------------------------------------

    def search(
        self, query: str | None = None, *, category: str | None = None, source: str | None = None,
        sensitivity: str | None = None, owner: str | None = None, pinned: bool | None = None,
        project: str | None = None, include_expired: bool = False, limit: int = 100,
    ) -> list[MemoryRecord]:
        if sensitivity is not None and sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError(f"sensitivity non valida: {sensitivity!r}")
        clauses: list[str] = []
        params: list = []
        reasons: list[str] = []
        for column, value, label in (
            ("category", category, "categoria"), ("source", source, "fonte"), ("sensitivity", sensitivity, "sensibilita'"),
            ("owner", owner, "proprietario"), ("project", project, "progetto"),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
                reasons.append(f"{label} = {value}")
        if pinned is not None:
            clauses.append("pinned = ?")
            params.append(1 if pinned else 0)
            reasons.append("fissato" if pinned else "non fissato")
        if query:
            clauses.append("(key LIKE ? OR value LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
            reasons.append(f"il testo contiene '{query}'")
        now = _now()
        if not include_expired:
            clauses.append("(expires_at IS NULL OR expires_at >= ?)")
            params.append(now)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.memory.lock:
            rows = self.memory.connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM memories {where} ORDER BY pinned DESC, updated_at DESC LIMIT ?", (*params, limit),
            ).fetchall()
        return [self._record(row, now, tuple(reasons)) for row in rows]

    @staticmethod
    def _record(row, now: str, why: tuple[str, ...] = ()) -> MemoryRecord:
        data = dict(row)
        data["pinned"] = bool(data["pinned"])
        data["has_embedding"] = bool(data["has_embedding"])
        return MemoryRecord(**data, expired=bool(data["expires_at"] and data["expires_at"] < now), why=why)

    def categories_of(self, key: str) -> list[str]:
        """Le categorie in cui esiste un ricordo con questa chiave esatta (per "dimentica X")."""
        with self.memory.lock:
            rows = self.memory.connection.execute(
                "SELECT DISTINCT category FROM memories WHERE key = ? ORDER BY category", (key,),
            ).fetchall()
        return [row[0] for row in rows]

    def get(self, key: str, category: str = "fact") -> MemoryRecord | None:
        with self.memory.lock:
            row = self.memory.connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM memories WHERE key = ? AND category = ?", (key, category),
            ).fetchone()
        return self._record(row, _now()) if row else None

    def explain(self, record: MemoryRecord) -> str:
        """Perche' questo ricordo esiste, in parole: chi l'ha creato, da dove, quanto e' sensibile, come vive."""
        author = record.created_by if record.created_by != "unknown" else "autore sconosciuto"
        lines = [
            f"Ricordo '{record.key}' (categoria {record.category}).",
            (f"Salvato il {record.created_at[:10]} da {author}; fonte: {self._describe_source(record.source)}; "
             f"ultima modifica il {record.updated_at[:10]}."),
            f"Sensibilita': {record.sensitivity}. Proprietario: {record.owner}.",
        ]
        if record.confidence is not None:
            lines.append(f"Confidenza registrata: {record.confidence:.2f}.")
        if record.valid_from or record.valid_until:
            lines.append(f"Valido dal {record.valid_from or '-'} al {record.valid_until or '-'}.")
        if record.expires_at:
            lines.append(f"{'E' + chr(39) + ' scaduto' if record.expired else 'Scade'} il {record.expires_at[:10]}.")
        else:
            lines.append("Non ha una scadenza.")
        if record.pinned:
            lines.append("E' fissato: le regole di retention non lo cancellano.")
        if record.use_count:
            lines.append(f"Usato {record.use_count} volte, l'ultima il {(record.last_used_at or '')[:10]}.")
        else:
            lines.append("Non e' mai stato usato per rispondere.")
        if record.has_embedding:
            lines.append("Ha un indice semantico (embedding) che verra' cancellato insieme a lui.")
        return "\n".join(lines)

    @staticmethod
    def _describe_source(source: str) -> str:
        if source == "user":
            return "detto esplicitamente dall'utente"
        if source == "inferred":
            return "dedotto da Jake (non detto dall'utente)"
        if source.startswith("agent:"):
            return f"deciso da un agente ({source[6:]})"
        return source

    # ---- F5.7.2 modificare, fissare, scadere, scollegare -------------------------------------------------------

    def edit(self, key: str, category: str = "fact", *, value: str | None = None, importance: int | None = None,
             sensitivity: str | None = None, owner: str | None = None, actor: str = "user") -> bool:
        """Modifica i campi dati. Cambiare il TESTO svuota l'embedding: l'indice semantico descriverebbe un
        testo che non c'e' piu' e farebbe ritrovare il ricordo per parole che non contiene. Il registro
        annota QUALI campi sono cambiati, non i valori."""
        if sensitivity is not None and sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError(f"sensitivity non valida: {sensitivity!r}")
        assignments, params, changed = [], [], []
        for column, new in (("value", value), ("importance", importance), ("sensitivity", sensitivity), ("owner", owner)):
            if new is not None:
                assignments.append(f"{column} = ?")
                params.append(new)
                changed.append(column)
        if not assignments:
            return False
        if value is not None:
            assignments.append("embedding = NULL")
        assignments.append("updated_at = ?")
        params.append(_now())
        with self.memory.lock:
            cursor = self.memory.connection.execute(
                f"UPDATE memories SET {', '.join(assignments)} WHERE key = ? AND category = ?", (*params, key, category),
            )
            if cursor.rowcount:
                self.memory._audit(key, category, "edited", actor, ", ".join(changed))
            self.memory.connection.commit()
            return cursor.rowcount > 0

    def pin(self, key: str, category: str = "fact", pinned: bool = True, actor: str = "user") -> bool:
        with self.memory.lock:
            cursor = self.memory.connection.execute(
                "UPDATE memories SET pinned = ? WHERE key = ? AND category = ?", (1 if pinned else 0, key, category),
            )
            if cursor.rowcount:
                self.memory._audit(key, category, "pinned" if pinned else "unpinned", actor)
            self.memory.connection.commit()
            return cursor.rowcount > 0

    def expire(self, key: str, category: str = "fact", actor: str = "user") -> bool:
        """Fa scadere adesso: sparisce da recall() ma resta sul disco (recuperabile) finche' non si cancella."""
        with self.memory.lock:
            cursor = self.memory.connection.execute(
                "UPDATE memories SET expires_at = ? WHERE key = ? AND category = ?", (_now(), key, category),
            )
            if cursor.rowcount:
                self.memory._audit(key, category, "expired", actor)
            self.memory.connection.commit()
            return cursor.rowcount > 0

    def unlink(self, key: str, category: str = "fact", predicate: str | None = None, actor: str = "user") -> int:
        """Rimuove le relazioni (in entrambe le direzioni) di un ricordo, senza toccare il ricordo."""
        extra, params = "", [key, category, key, category]
        if predicate:
            extra, params = " AND predicate = ?", [key, category, predicate, key, category, predicate]
            sql = ("DELETE FROM memory_relations WHERE (subject_key = ? AND subject_category = ?" + extra +
                   ") OR (object_key = ? AND object_category = ?" + extra + ")")
        else:
            sql = ("DELETE FROM memory_relations WHERE (subject_key = ? AND subject_category = ?) "
                   "OR (object_key = ? AND object_category = ?)")
        with self.memory.lock:
            cursor = self.memory.connection.execute(sql, params)
            if cursor.rowcount:
                self.memory._audit(key, category, "unlinked", actor, f"{cursor.rowcount} relazioni")
            self.memory.connection.commit()
            return cursor.rowcount

    # ---- F5.7.3 chi ha creato e usato -----------------------------------------------------------------------------

    def record_use(self, key: str, category: str = "fact", actor: str = "jake", context: str = "") -> bool:
        with self.memory.lock:
            cursor = self.memory.connection.execute(
                "UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE key = ? AND category = ?",
                (_now(), key, category),
            )
            if cursor.rowcount:
                self.memory._audit(key, category, "used", actor, context)
            self.memory.connection.commit()
            return cursor.rowcount > 0

    def audit_trail(self, key: str, category: str = "fact") -> list[dict]:
        with self.memory.lock:
            rows = self.memory.connection.execute(
                "SELECT event, actor, at, detail FROM memory_audit WHERE memory_key = ? AND memory_category = ? ORDER BY id",
                (key, category),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---- F5.7.6 cancellazione vera con ricevuta ----------------------------------------------------------------------

    def register_index(self, name: str, remove: Callable[[str, str], None], contains: Callable[[str, str], bool],
                       clear_all: Callable[[], None] | None = None) -> None:
        """Un indice derivato FUORI dal database (indice degli esempi, NEST...). `remove(key, category)` toglie il
        ricordo, `contains(key, category)` dice se c'e' ancora, `clear_all()` (opzionale) svuota tutto l'indice."""
        self._indexes[name] = (remove, contains, clear_all)

    def delete(self, key: str, category: str = "fact", actor: str = "user") -> DeletionReceipt | None:
        """Cancella il ricordo, le sue relazioni, il suo registro eventi e lo toglie da ogni indice registrato.
        None se il ricordo non esiste. La ricevuta dice, indice per indice, se e' davvero sparito."""
        with self.memory.lock:
            existing = self.memory.connection.execute(
                "SELECT value FROM memories WHERE key = ? AND category = ?", (key, category),
            ).fetchone()
            if existing is None:
                return None
            value = existing["value"]
            connection = self.memory.connection
            removed = {
                "memory_relations": connection.execute(
                    "DELETE FROM memory_relations WHERE (subject_key = ? AND subject_category = ?) "
                    "OR (object_key = ? AND object_category = ?)", (key, category, key, category),
                ).rowcount,
                "memory_audit": connection.execute(
                    "DELETE FROM memory_audit WHERE memory_key = ? AND memory_category = ?", (key, category),
                ).rowcount,
                "memories": connection.execute("DELETE FROM memories WHERE key = ? AND category = ?", (key, category)).rowcount,
                "conversation_history_redacted": self._redact_history(value),
            }
            connection.commit()
            derived: dict[str, dict] = {}
            for name, (remove, contains, _) in self._indexes.items():
                try:
                    remove(key, category)
                    still = bool(contains(key, category))
                except Exception as exc:
                    derived[name] = {"removed": False, "still_present": True, "error": type(exc).__name__}
                    continue
                derived[name] = {"removed": not still, "still_present": still}
            still_in_db = connection.execute(
                "SELECT 1 FROM memories WHERE key = ? AND category = ?", (key, category),
            ).fetchone() is not None
        residue = self._residue_check(value)
        receipt = DeletionReceipt(
            _hash_memory(key, category), _now(), actor, removed, derived, residue,
            verified=(not still_in_db and residue != "found" and all(not d["still_present"] for d in derived.values())),
        )
        self._store_receipt(receipt)
        return receipt

    def _redact_history(self, value: str) -> int:
        """Una copia del ricordo vive anche nella cronologia delle conversazioni ("ricordati che il mio
        indirizzo e' via X"): dimenticare vuol dire toglierla anche da li'. Il valore diventa
        "[dimenticato]" nelle righe che lo contengono (senza distinguere maiuscole); valori troppo corti
        per essere riconosciuti con sicurezza (< 4 caratteri) non si toccano. Chiamata sotto il lock."""
        if len(value.strip()) < 4:
            return 0
        pattern = re.compile(re.escape(value.strip()), re.IGNORECASE)
        connection = self.memory.connection
        rows = connection.execute(
            "SELECT id, text FROM conversation_history WHERE instr(lower(text), lower(?)) > 0", (value.strip(),),
        ).fetchall()
        for row in rows:
            connection.execute("UPDATE conversation_history SET text = ? WHERE id = ?",
                               (pattern.sub("[dimenticato]", row["text"]), row["id"]))
        return len(rows)

    def _residue_check(self, value: str) -> str:
        """Cerca i byte del valore appena cancellato nel file del database. "found" = ce n'e' ancora una copia
        (secure_delete non ha funzionato o c'e' una copia in un file accanto); "not_checked" per un database in memoria."""
        db_path = getattr(self.memory, "db_path", None)
        if db_path is None or not Path(db_path).exists() or len(value) < 6:
            return "not_checked"
        needle = value.encode("utf-8")
        for candidate in (Path(db_path), Path(str(db_path) + "-wal"), Path(str(db_path) + "-journal")):
            if candidate.exists() and needle in candidate.read_bytes():
                return "found"
        return "clean"

    def _store_receipt(self, receipt: DeletionReceipt) -> None:
        self.receipts.append(receipt)
        if self.receipts_path is not None:
            self.receipts_path.parent.mkdir(parents=True, exist_ok=True)
            with self.receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt.to_dict(), ensure_ascii=False) + "\n")

    def purge_everything(self, confirm: str, remove_backups: bool = True, actor: str = "user") -> dict:
        """Cancella TUTTA la memoria (ricordi, relazioni, cronologia, registro eventi), svuota gli indici derivati,
        compatta il file e toglie i backup locali. Richiede la frase esatta: non parte per errore. Non tocca
        promemoria, todo e workflow (non sono memoria). La ricevuta elenca cosa NON puo' raggiungere."""
        if confirm != CONFIRM_PHRASE:
            raise PermissionError(f"per cancellare tutta la memoria serve la frase esatta '{CONFIRM_PHRASE}'")
        connection = self.memory.connection
        with self.memory.lock:
            samples = [r[0] for r in connection.execute(
                "SELECT value FROM memories WHERE length(value) >= 8 LIMIT 50").fetchall()]
            counts = {}
            for table in ("memories", "memory_relations", "conversation_history", "memory_audit"):
                counts[table] = connection.execute(f"DELETE FROM {table}").rowcount
            connection.commit()
            derived = {}
            for name, (_, _, clear_all) in self._indexes.items():
                if clear_all is None:
                    derived[name] = {"cleared": False, "reason": "l'indice non espone clear_all"}
                    continue
                try:
                    clear_all()
                    derived[name] = {"cleared": True}
                except Exception as exc:
                    derived[name] = {"cleared": False, "reason": type(exc).__name__}
            connection.execute("VACUUM")
        backups_removed = 0
        db_path = getattr(self.memory, "db_path", None)
        if remove_backups and db_path is not None:
            directory = backups_dir_for(Path(db_path))
            if directory.is_dir():
                backups_removed = sum(1 for _ in directory.glob("*.bak"))
                shutil.rmtree(directory, ignore_errors=True)
        residue = "not_checked"
        if db_path is not None and Path(db_path).exists():
            data = Path(db_path).read_bytes()
            residue = "found" if any(sample.encode("utf-8") in data for sample in samples) else "clean"
        remaining = {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("memories", "memory_relations", "conversation_history", "memory_audit")}
        receipt = {
            "purged_at": _now(), "actor": actor, "rows_removed": counts, "rows_remaining": remaining,
            "derived_indexes": derived, "vacuumed": True, "local_backups_removed": backups_removed,
            "residue_check": residue, "samples_checked": len(samples),
            "not_reachable": [
                "copie esportate dall'utente (export JSON/Markdown, backup cifrati salvati altrove)",
                "promemoria, todo e workflow (non sono memoria e non vengono toccati)",
            ],
            "verified": all(v == 0 for v in remaining.values()) and residue != "found"
            and all(d.get("cleared", False) for d in derived.values()),
        }
        if self.receipts_path is not None:
            self.receipts_path.parent.mkdir(parents=True, exist_ok=True)
            with self.receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"purge": receipt}, ensure_ascii=False) + "\n")
        return receipt

    # ---- F5.7.4 esportare ------------------------------------------------------------------------------------------------

    def export_payload(self, include_secret: bool = False, include_embeddings: bool = False) -> dict:
        """Tutto cio' che sta nella memoria in una struttura machine-readable. I ricordi 'secret' NON si
        esportano senza chiederlo: un export e' pensato per essere letto e condiviso."""
        with self.memory.lock:
            connection = self.memory.connection
            columns = _RECORD_COLUMNS.replace("embedding IS NOT NULL AS has_embedding", "embedding")
            rows = connection.execute(f"SELECT {columns} FROM memories ORDER BY category, key").fetchall()
            relations = [dict(r) for r in connection.execute(
                "SELECT subject_key, subject_category, predicate, object_key, object_category, created_at FROM memory_relations"
            ).fetchall()]
        records, excluded = [], 0
        for row in rows:
            data = dict(row)
            if data["sensitivity"] == "secret" and not include_secret:
                excluded += 1
                continue
            if not include_embeddings:
                data.pop("embedding", None)
            data["pinned"] = bool(data["pinned"])
            records.append(data)
        keys = {(r["key"], r["category"]) for r in records}
        relations = [r for r in relations
                     if (r["subject_key"], r["subject_category"]) in keys and (r["object_key"], r["object_category"]) in keys]
        return {
            "format": "jake-memory-export", "version": 1, "schema_version": SCHEMA_VERSION, "exported_at": _now(),
            "records": records, "relations": relations, "excluded_secret": excluded,
        }

    def export_json(self, path: Path, **options) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.export_payload(**options), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def export_markdown(self, path: Path, include_secret: bool = False) -> Path:
        payload = self.export_payload(include_secret=include_secret)
        lines = [f"# Memoria di Jake - esportata il {payload['exported_at'][:10]}", ""]
        if payload["excluded_secret"]:
            lines += [f"> {payload['excluded_secret']} ricordi segreti non sono inclusi.", ""]
        current = None
        for record in payload["records"]:
            if record["category"] != current:
                current = record["category"]
                lines += [f"## {current}", ""]
            lines.append(f"- **{record['key']}**: {record['value']}")
            details = [f"sensibilita' {record['sensitivity']}", f"fonte {record['source']}"]
            if record["pinned"]:
                details.append("fissato")
            if record["expires_at"]:
                details.append(f"scade {record['expires_at'][:10]}")
            lines.append(f"  - {', '.join(details)}; salvato {record['created_at'][:10]}")
        lines.append("")
        path = Path(path)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


# ---- F5.7.7 retention per profilo e categoria ------------------------------------------------------------------------------

@dataclass
class RetentionPolicy:
    """Quanto tempo si tengono i ricordi NON fissati, in giorni dall'ultima modifica. La regola piu' breve tra
    quella della categoria e quella della sensibilita' vince; `default_days` vale per il resto (None = per sempre)."""

    by_category: dict[str, float] = field(default_factory=dict)
    by_sensitivity: dict[str, float] = field(default_factory=dict)
    default_days: float | None = None
    history_days: float | None = None

    def rule_for(self, category: str, sensitivity: str) -> tuple[float | None, str]:
        candidates = []
        if category in self.by_category:
            candidates.append((self.by_category[category], f"categoria {category}"))
        if sensitivity in self.by_sensitivity:
            candidates.append((self.by_sensitivity[sensitivity], f"sensibilita' {sensitivity}"))
        if candidates:
            return min(candidates, key=lambda c: c[0])
        return (self.default_days, "default") if self.default_days is not None else (None, "nessuna")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> RetentionPolicy:
        data = data or {}
        for section in ("by_category", "by_sensitivity"):
            for name, days in (data.get(section) or {}).items():
                if not isinstance(days, (int, float)) or isinstance(days, bool) or days <= 0:
                    raise ValueError(f"retention {section}[{name!r}] deve essere un numero di giorni > 0")
        for name in ("default_days", "history_days"):
            value = data.get(name)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0):
                raise ValueError(f"retention {name} deve essere un numero di giorni > 0")
        unknown = set(data.get("by_sensitivity") or {}) - set(SENSITIVITY_LEVELS)
        if unknown:
            raise ValueError(f"sensibilita' sconosciute nella retention: {sorted(unknown)}")
        return cls(dict(data.get("by_category") or {}), dict(data.get("by_sensitivity") or {}),
                   data.get("default_days"), data.get("history_days"))


@dataclass
class RetentionReport:
    candidates: list[dict]
    deleted: int
    history_deleted: int
    dry_run: bool


def apply_retention(dashboard: MemoryPrivacyDashboard, policy: RetentionPolicy, dry_run: bool = True,
                    now: datetime | None = None, actor: str = "retention") -> RetentionReport:
    """Trova (e, con `dry_run=False`, cancella con ricevuta) i ricordi non fissati piu' vecchi della regola che
    li riguarda. Di default NON cancella: un'applicazione della retention e' sempre prima un'anteprima."""
    now = now or datetime.now(timezone.utc)
    candidates: list[dict] = []
    for record in dashboard.search(include_expired=True, limit=100_000):
        if record.pinned:
            continue
        days, why = policy.rule_for(record.category, record.sensitivity)
        if days is None:
            continue
        cutoff = (now - timedelta(days=days)).isoformat()
        if record.updated_at < cutoff:
            candidates.append({"key": record.key, "category": record.category, "rule": why, "days": days,
                               "updated_at": record.updated_at})
    deleted = history_deleted = 0
    if not dry_run:
        for item in candidates:
            if dashboard.delete(item["key"], item["category"], actor=actor) is not None:
                deleted += 1
        if policy.history_days is not None:
            history_deleted = dashboard.memory.purge_history_older_than(policy.history_days)
    return RetentionReport(candidates, deleted, history_deleted, dry_run)


def policy_for_profile(namespace) -> RetentionPolicy:
    """La retention del profilo (`namespace.preferences["retention"]`, vedi core/profiles.py): profili diversi
    possono avere regole diverse sulla propria memoria (F5.7.7 + F2.7.4). Un ospite non ne ha: la sua memoria
    e' temporanea e sparisce alla chiusura."""
    if not getattr(namespace, "persistent", True):
        return RetentionPolicy()
    return RetentionPolicy.from_dict(getattr(namespace, "preferences", {}).get("retention"))

