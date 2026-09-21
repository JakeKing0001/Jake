"""Test per core/memory_schema.py e per i metadati F5.1.3 in MemoryManager (F5.1.1, F5.1.3-F5.1.6).
Database veri su cartelle temporanee: gli upgrade si provano su file costruiti con lo schema VECCHIO
(creato a mano con SQL), non su mock."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import memory_schema
from core.memory_manager import MemoryManager
from core.memory_schema import (
    SCHEMA_VERSION, MemoryCorruptError, MigrationError, SchemaTooNewError, create_backup, current_version,
    file_is_healthy, integrity_check, list_backups, recover_from_backup,
)


def _legacy_db(path: Path, with_modern_columns: bool = True) -> None:
    """Un database com'era PRIMA del versionamento, con dati veri dentro."""
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, value TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'fact', importance INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(key, category));
        CREATE TABLE conversation_history (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE memory_relations (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_key TEXT NOT NULL, subject_category TEXT NOT NULL,
            predicate TEXT NOT NULL, object_key TEXT NOT NULL, object_category TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(subject_key, subject_category, predicate, object_key, object_category));
        """
    )
    if with_modern_columns:
        connection.executescript(
            "ALTER TABLE memories ADD COLUMN embedding TEXT; ALTER TABLE memories ADD COLUMN project TEXT;"
            "ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'user'; ALTER TABLE memories ADD COLUMN expires_at TEXT;"
        )
    connection.execute(
        "INSERT INTO memories (key, value, category, importance, created_at, updated_at) VALUES "
        "('compleanno', '5 marzo', 'fact', 3, '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO memories (key, value, category, importance, created_at, updated_at) VALUES "
        "('caffe', 'senza zucchero', 'preference', 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    connection.execute("INSERT INTO conversation_history (role, text, created_at) VALUES ('user', 'ciao', '2026-01-01T00:00:00+00:00')")
    connection.commit()
    connection.close()


class FreshDatabaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "memory.db"

    def test_a_new_database_is_created_at_the_current_version_with_no_backup(self):
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        self.assertEqual(memory.migration_report.applied, [1, 2])
        self.assertEqual(memory.migration_report.to_version, SCHEMA_VERSION)
        self.assertIsNone(memory.migration_report.backup_path)  # niente dati: niente da salvare
        self.assertEqual(current_version(memory.connection), SCHEMA_VERSION)
        self.assertEqual(list_backups(self.path), [])

    def test_the_new_metadata_columns_exist_with_explicit_defaults(self):
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        memory.remember("k", "valore")
        row = memory.connection.execute("SELECT sensitivity, owner, created_by, confidence, valid_from, pinned, use_count FROM memories").fetchone()
        self.assertEqual(tuple(row), ("unknown", "unknown", "user", None, None, 0, 0))  # created_by dalla fonte

    def test_reopening_an_up_to_date_database_does_nothing(self):
        MemoryManager(self.path).close()
        again = MemoryManager(self.path)
        self.addCleanup(again.close)
        self.assertEqual((again.migration_report.applied, again.migration_report.backup_path), ([], None))

    def test_secure_delete_is_on(self):
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        self.assertEqual(memory.connection.execute("PRAGMA secure_delete").fetchone()[0], 1)


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "memory.db"

    def test_a_pre_versioning_database_is_upgraded_keeping_every_memory_and_recall_unchanged(self):
        _legacy_db(self.path)
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        self.assertEqual(memory.migration_report.from_version, 0)
        self.assertEqual(memory.migration_report.applied, [1, 2])
        recalled = {r["key"]: r for r in memory.recall(limit=10)}
        self.assertEqual(recalled["compleanno"]["value"], "5 marzo")
        self.assertEqual(recalled["compleanno"]["importance"], 3)
        self.assertEqual(set(recalled["compleanno"]), {"key", "value", "category", "importance", "updated_at", "project", "source", "expires_at"})
        row = memory.connection.execute("SELECT sensitivity, owner, use_count FROM memories WHERE key='caffe'").fetchone()
        self.assertEqual(tuple(row), ("unknown", "unknown", 0))  # i vecchi ricordi ereditano "sconosciuto", non un valore inventato
        self.assertEqual(len(memory.get_recent_history()), 1)

    def test_a_backup_is_taken_before_touching_existing_data_and_is_a_faithful_old_copy(self):
        _legacy_db(self.path)
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        backup = memory.migration_report.backup_path
        assert backup is not None
        self.assertTrue(backup.exists())
        copy = sqlite3.connect(backup)
        try:
            columns = {r[1] for r in copy.execute("PRAGMA table_info(memories)").fetchall()}
            self.assertNotIn("sensitivity", columns)  # e' la versione PRIMA della migrazione
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 2)
        finally:
            copy.close()

    def test_a_very_old_database_without_the_memory_2_columns_is_also_upgraded(self):
        _legacy_db(self.path, with_modern_columns=False)
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        columns = {r[1] for r in memory.connection.execute("PRAGMA table_info(memories)").fetchall()}
        self.assertTrue({"embedding", "project", "source", "expires_at", "sensitivity", "pinned"} <= columns)
        self.assertEqual(memory.recall(key="compleanno")[0]["source"], "user")

    def test_the_upgrade_is_idempotent_when_columns_already_exist(self):
        _legacy_db(self.path)
        MemoryManager(self.path).close()
        # simula un file a cui le colonne nuove erano gia' state aggiunte a mano ma la versione no
        connection = sqlite3.connect(self.path)
        connection.execute("DELETE FROM schema_version WHERE version = 2")
        connection.commit()
        connection.close()
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        self.assertEqual(memory.migration_report.applied, [2])

    def test_backups_are_pruned_to_the_configured_number(self):
        _legacy_db(self.path)
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        for i in range(8):
            create_backup(memory.connection, self.path, f"manual{i}")
        self.assertLessEqual(len(list_backups(self.path)), memory_schema.BACKUPS_TO_KEEP)


class UnsupportedAndFailedMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "memory.db"

    def test_a_database_from_the_future_is_refused_and_left_untouched(self):
        MemoryManager(self.path).close()
        connection = sqlite3.connect(self.path)
        connection.execute("INSERT INTO schema_version(version, applied_at, description) VALUES (99, 'x', 'futura')")
        connection.commit()
        connection.close()
        before = self.path.read_bytes()
        with self.assertRaises(SchemaTooNewError):
            MemoryManager(self.path)
        self.assertEqual(self.path.read_bytes(), before)  # non si scrive nulla su un file piu' nuovo del codice
        self.assertEqual(list_backups(self.path), [])

    def test_a_failing_migration_is_rolled_back_including_its_ddl(self):
        MemoryManager(self.path).close()

        def broken(conn):
            conn.execute("ALTER TABLE memories ADD COLUMN esperimento TEXT")
            raise RuntimeError("guasto a meta' migrazione")

        migrations = [*memory_schema.MIGRATIONS, memory_schema.Migration(3, "rotta", broken)]
        with mock.patch.object(memory_schema, "MIGRATIONS", migrations), mock.patch.object(memory_schema, "SCHEMA_VERSION", 3):
            with self.assertRaises(MigrationError) as ctx:
                MemoryManager(self.path)
        self.assertEqual(ctx.exception.version, 3)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(current_version(connection), 2)  # la versione non e' avanzata
            columns = {r[1] for r in connection.execute("PRAGMA table_info(memories)").fetchall()}
            self.assertNotIn("esperimento", columns)  # ...e la colonna aggiunta a meta' e' sparita
        finally:
            connection.close()

    def test_a_process_killed_mid_migration_leaves_the_file_as_it_was(self):
        _legacy_db(self.path)
        connection = sqlite3.connect(self.path)
        connection.isolation_level = None
        connection.execute("BEGIN IMMEDIATE")
        memory_schema._v1_baseline(connection)
        connection.execute("ALTER TABLE memories ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'unknown'")
        connection.close()  # come un processo che muore prima del COMMIT
        reopened = MemoryManager(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.migration_report.from_version, 0)  # nessuna versione era stata scritta
        self.assertEqual(reopened.recall(key="compleanno")[0]["value"], "5 marzo")

    def test_the_backup_taken_before_a_failed_migration_is_still_there(self):
        _legacy_db(self.path)

        def broken(conn):
            raise RuntimeError("no")

        migrations = [memory_schema.MIGRATIONS[0], memory_schema.Migration(2, "rotta", broken)]
        with mock.patch.object(memory_schema, "MIGRATIONS", migrations):
            with self.assertRaises(MigrationError):
                MemoryManager(self.path)
        self.assertEqual(len(list_backups(self.path)), 1)


class IntegrityAndRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "memory.db"

    def test_a_healthy_database_has_no_problems(self):
        memory = MemoryManager(self.path)
        self.addCleanup(memory.close)
        memory.remember("k", "v")
        self.assertEqual(integrity_check(memory.connection), [])
        self.assertTrue(file_is_healthy(self.path))

    def test_garbage_is_not_healthy(self):
        self.path.write_bytes(b"questo non e' un database sqlite" * 50)
        self.assertFalse(file_is_healthy(self.path))
        self.assertFalse(file_is_healthy(Path(self._tmp.name) / "non_esiste.db"))

    def test_a_corrupt_database_is_set_aside_and_the_latest_good_backup_restored(self):
        memory = MemoryManager(self.path)
        memory.remember("segreto di famiglia", "la ricetta della nonna")
        create_backup(memory.connection, self.path, "manual")
        memory.close()
        self.path.write_bytes(b"\x00garbage" * 2000)
        restored_from = recover_from_backup(self.path)
        self.assertTrue(restored_from.exists())
        asides = list(self.path.parent.glob("memory.db.corrupt-*"))
        self.assertEqual(len(asides), 1)  # il file rotto e' stato conservato, non buttato
        reopened = MemoryManager(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.recall(key="segreto di famiglia")[0]["value"], "la ricetta della nonna")

    def test_a_corrupt_backup_is_skipped_for_an_older_good_one(self):
        memory = MemoryManager(self.path)
        memory.remember("k", "v")
        good = create_backup(memory.connection, self.path, "good")
        bad = create_backup(memory.connection, self.path, "bad")
        memory.close()
        bad.write_bytes(b"rotto" * 100)
        import os
        os.utime(good, (1, 1))  # il buono e' il piu' vecchio
        self.path.write_bytes(b"\x00" * 100)
        self.assertEqual(recover_from_backup(self.path), good)

    def test_without_any_valid_backup_nothing_is_overwritten(self):
        self.path.write_bytes(b"\x00garbage" * 100)
        before = self.path.read_bytes()
        with self.assertRaises(MemoryCorruptError):
            recover_from_backup(self.path)
        self.assertEqual(self.path.read_bytes(), before)  # il file rotto resta li': potrebbe servire a un esperto


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.memory = MemoryManager(Path(self._tmp.name) / "m.db")
        self.addCleanup(self.memory.close)

    def _row(self, key="k"):
        return dict(self.memory.connection.execute("SELECT * FROM memories WHERE key = ?", (key,)).fetchone())

    def test_metadata_can_be_given_when_remembering(self):
        self.memory.remember("k", "v", sensitivity="sensitive", owner="davide", confidence=0.8,
                             valid_from="2026-01-01", valid_until="2026-12-31", created_by="jake")
        row = self._row()
        self.assertEqual((row["sensitivity"], row["owner"], row["confidence"], row["created_by"]), ("sensitive", "davide", 0.8, "jake"))
        self.assertEqual((row["valid_from"], row["valid_until"]), ("2026-01-01", "2026-12-31"))

    def test_updating_the_text_keeps_the_metadata_chosen_before(self):
        self.memory.remember("k", "v1", sensitivity="secret", owner="davide")
        self.memory.remember("k", "v2")
        row = self._row()
        self.assertEqual((row["value"], row["sensitivity"], row["owner"]), ("v2", "secret", "davide"))

    def test_the_first_author_stays_the_author(self):
        self.memory.remember("k", "v1", created_by="jake")
        self.memory.remember("k", "v2", created_by="davide")
        self.assertEqual(self._row()["created_by"], "jake")

    def test_invalid_metadata_is_rejected_before_writing_anything(self):
        for kwargs in ({"sensitivity": "top-secret"}, {"confidence": 1.5}, {"confidence": -0.1},
                       {"valid_from": "2026-12-01", "valid_until": "2026-01-01"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.memory.remember("k", "v", **kwargs)
        self.assertEqual(self.memory.count_memories(), 0)

    def test_creation_and_update_are_recorded_in_the_audit_trail(self):
        self.memory.remember("k", "v1", created_by="davide")
        self.memory.remember("k", "v2")
        events = [r["event"] for r in self.memory.connection.execute("SELECT event FROM memory_audit ORDER BY id").fetchall()]
        self.assertEqual(events, ["created", "updated"])

    def test_the_audit_trail_of_one_memory_is_bounded(self):
        self.memory.remember("k", "v")
        for _ in range(self.memory.MAX_AUDIT_EVENTS_PER_MEMORY + 30):
            self.memory._audit("k", "fact", "used", "jake")
        self.memory.connection.commit()
        count = self.memory.connection.execute("SELECT COUNT(*) FROM memory_audit WHERE memory_key='k'").fetchone()[0]
        self.assertEqual(count, self.memory.MAX_AUDIT_EVENTS_PER_MEMORY)

    def test_existing_behaviour_of_remember_and_recall_is_unchanged(self):
        self.memory.remember("compleanno", "5 marzo", importance=2, source="inferred", ttl_days=1)
        result = self.memory.recall(key="compleanno")[0]
        self.assertEqual((result["value"], result["importance"], result["source"]), ("5 marzo", 2, "inferred"))
        self.assertIsNotNone(result["expires_at"])


if __name__ == "__main__":
    unittest.main()
