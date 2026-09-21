"""Schema versionato e migrazioni transazionali per la memoria (F5.1.1, F5.1.3, F5.1.5, F5.1.6).

Prima di questo modulo lo schema di `MemoryManager` cresceva con `ALTER TABLE ... ADD COLUMN` lanciati a
ogni apertura, senza sapere in che versione fosse un database: impossibile dire "questo file e' piu'
nuovo del codice", impossibile tornare indietro se una modifica andava male, impossibile provare un
upgrade su una copia. Qui:

- una tabella `schema_version` dice in che versione e' il file (un database creato prima di questo
  modulo, senza tabella ma con `memories`, e' trattato come versione 0 e portato avanti);
- ogni migrazione gira in UNA transazione: se solleva a meta', SQLite annulla tutto (anche il DDL) e la
  versione non avanza - il file resta com'era, e un test lo prova interrompendo una migrazione a meta';
- PRIMA di toccare un database che ha gia' dati si crea un backup consistente (API di backup di SQLite,
  non una copia del file mentre e' aperto) accanto al file; se il file e' piu' NUOVO del codice ci si
  rifiuta di aprirlo (`SchemaTooNewError`): un downgrade non e' supportato e scrivere con uno schema
  piu' vecchio rischierebbe di perdere colonne che non conosce;
- un file corrotto non viene mai sovrascritto da un database vuoto: `recover_from_backup` mette da parte il
  file rotto (`.corrupt-<timestamp>`) e ripristina il backup piu' recente che passa l'integrity check; se
  non ce n'e' nessuno, `MemoryCorruptError` e il file resta al suo posto.

Metadati obbligatori o esplicitamente sconosciuti (F5.1.3): sensitivity, owner, created_by hanno default
'unknown' (non NULL, non una stringa vuota: "non lo so" e' un valore dichiarato); confidence, valid_from e
valid_until sono nullable con lo stesso significato. `recorded time` e' `created_at`, gia' esistente."""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

SENSITIVITY_LEVELS = ("unknown", "public", "personal", "sensitive", "secret")
BACKUPS_TO_KEEP = 5


class SchemaTooNewError(Exception):
    """Il file e' di una versione piu' recente del codice: aprirlo in scrittura non e' sicuro."""


class MigrationError(Exception):
    def __init__(self, version: int, cause: Exception) -> None:
        super().__init__(f"migrazione alla versione {version} fallita e annullata: {cause}")
        self.version = version
        self.cause = cause


class MemoryCorruptError(Exception):
    """Il database e' corrotto e non c'e' un backup valido da cui ripartire."""


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    apply: Callable[[sqlite3.Connection], None]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _v1_baseline(conn: sqlite3.Connection) -> None:
    """Lo schema com'era prima del versionamento (v0.2 + Memory 2.0), tutto idempotente: un database
    creato da una versione qualunque del progetto arriva allo stesso punto."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, value TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'fact', importance INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(key, category))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS conversation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memory_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subject_key TEXT NOT NULL, subject_category TEXT NOT NULL,
            predicate TEXT NOT NULL, object_key TEXT NOT NULL, object_category TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(subject_key, subject_category, predicate, object_key, object_category))"""
    )
    _add_column_if_missing(conn, "memories", "embedding", "TEXT")
    _add_column_if_missing(conn, "memories", "project", "TEXT")
    _add_column_if_missing(conn, "memories", "source", "TEXT NOT NULL DEFAULT 'user'")
    _add_column_if_missing(conn, "memories", "expires_at", "TEXT")


def _v2_metadata(conn: sqlite3.Connection) -> None:
    """F5.1.3: sensibilita', proprietario, autore, confidenza, tempo di validita', pin e uso di un ricordo,
    piu' un registro degli eventi (chi ha creato, modificato, usato: F5.7.3)."""
    _add_column_if_missing(conn, "memories", "sensitivity", "TEXT NOT NULL DEFAULT 'unknown'")
    _add_column_if_missing(conn, "memories", "owner", "TEXT NOT NULL DEFAULT 'unknown'")
    _add_column_if_missing(conn, "memories", "created_by", "TEXT NOT NULL DEFAULT 'unknown'")
    _add_column_if_missing(conn, "memories", "confidence", "REAL")
    _add_column_if_missing(conn, "memories", "valid_from", "TEXT")
    _add_column_if_missing(conn, "memories", "valid_until", "TEXT")
    _add_column_if_missing(conn, "memories", "pinned", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "memories", "last_used_at", "TEXT")
    _add_column_if_missing(conn, "memories", "use_count", "INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memory_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, memory_key TEXT NOT NULL, memory_category TEXT NOT NULL,
            event TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'unknown', at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_audit_memory ON memory_audit(memory_key, memory_category)")


MIGRATIONS: list[Migration] = [
    Migration(1, "schema di base (memorie, cronologia, relazioni) e colonne storiche", _v1_baseline),
    Migration(2, "metadati F5.1.3 (sensibilita', owner, autore, confidenza, validita', pin, uso) e registro eventi", _v2_metadata),
]
SCHEMA_VERSION = MIGRATIONS[-1].version


@dataclass
class MigrationReport:
    from_version: int
    to_version: int
    applied: list[int] = field(default_factory=list)
    backup_path: Path | None = None


def current_version(conn: sqlite3.Connection) -> int:
    """0 se il file non ha ancora `schema_version` (nuovo, o creato prima del versionamento)."""
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _has_data(conn: sqlite3.Connection) -> bool:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in tables if not t.startswith("sqlite_") and t != "schema_version")


def backups_dir_for(db_path: Path) -> Path:
    return Path(db_path).parent / "backups"


def create_backup(conn: sqlite3.Connection, db_path: Path, label: str) -> Path:
    """Backup consistente con l'API di SQLite (funziona anche con il database aperto e in uso)."""
    directory = backups_dir_for(db_path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{Path(db_path).stem}.{label}.{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}.bak"
    destination = sqlite3.connect(target)
    try:
        conn.backup(destination)
    finally:
        destination.close()
    _prune_backups(db_path)
    return target


def list_backups(db_path: Path) -> list[Path]:
    directory = backups_dir_for(db_path)
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"{Path(db_path).stem}.*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)


def _prune_backups(db_path: Path, keep: int = BACKUPS_TO_KEEP) -> None:
    for old in list_backups(db_path)[keep:]:
        old.unlink(missing_ok=True)


def ensure_schema(conn: sqlite3.Connection, db_path: Path | None) -> MigrationReport:
    """Porta il database alla versione corrente. Con dati esistenti e un percorso su disco fa PRIMA un backup."""
    start = current_version(conn)
    if start > SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"il database e' alla versione {start}, questo Jake conosce fino alla {SCHEMA_VERSION}: "
            "aggiorna Jake (un downgrade dello schema non e' supportato e potrebbe far perdere dati)"
        )
    report = MigrationReport(start, start)
    pending = [m for m in MIGRATIONS if m.version > start]
    if not pending:
        return report
    if db_path is not None and Path(db_path).exists() and start >= 0 and _has_data(conn):
        report.backup_path = create_backup(conn, Path(db_path), f"v{start}")
    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # autocommit: BEGIN/COMMIT espliciti, cosi' anche il DDL sta in UNA transazione
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL DEFAULT '')"
        )
        for migration in pending:
            conn.execute("BEGIN IMMEDIATE")
            try:
                migration.apply(conn)
                conn.execute(
                    "INSERT INTO schema_version(version, applied_at, description) VALUES (?, ?, ?)",
                    (migration.version, time.strftime("%Y-%m-%dT%H:%M:%S"), migration.description),
                )
                conn.execute("COMMIT")
            except Exception as exc:
                conn.execute("ROLLBACK")
                raise MigrationError(migration.version, exc) from exc
            report.applied.append(migration.version)
            report.to_version = migration.version
    finally:
        conn.isolation_level = previous_isolation
    return report


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    """Lista dei problemi trovati (vuota = database integro): integrity_check di SQLite + chiavi esterne."""
    problems: list[str] = []
    try:
        rows = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
    except sqlite3.DatabaseError as exc:
        return [f"integrity_check non eseguibile: {exc}"]
    if rows != ["ok"]:
        problems.extend(rows)
    try:
        problems.extend(f"chiave esterna: {tuple(row)}" for row in conn.execute("PRAGMA foreign_key_check").fetchall())
    except sqlite3.DatabaseError as exc:
        problems.append(f"foreign_key_check non eseguibile: {exc}")
    return problems


def file_is_healthy(path: Path) -> bool:
    """Un file .db/.bak si apre e passa l'integrity check (mai una scrittura)."""
    try:
        connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        return not integrity_check(connection)
    finally:
        connection.close()


def recover_from_backup(db_path: Path) -> Path:
    """Mette da parte il database rotto (`.corrupt-<ts>`, MAI cancellato) e ripristina il backup piu' recente
    che passa l'integrity check. `MemoryCorruptError` se non ce n'e' nessuno: il file rotto resta dov'e'."""
    db_path = Path(db_path)
    for candidate in list_backups(db_path):
        if file_is_healthy(candidate):
            aside = db_path.with_name(f"{db_path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
            if db_path.exists():
                os.replace(db_path, aside)
            for suffix in ("-wal", "-shm"):
                leftover = db_path.with_name(db_path.name + suffix)
                if leftover.exists():
                    leftover.unlink()
            shutil.copy2(candidate, db_path)
            return candidate
    raise MemoryCorruptError(
        f"{db_path} e' corrotto e non ci sono backup validi in {backups_dir_for(db_path)}: il file e' stato lasciato com'e'"
    )
