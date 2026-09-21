"""Test per core/memory_privacy.py (F5.7.1-F5.7.4, F5.7.6, F5.7.7) e core/memory_backup.py (F5.7.5).
Database reali su cartelle temporanee; il KDF si abbassa a 2^12 nei test per la velocita' (in produzione
il default e' 2^15)."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from core.memory_backup import (
    MIN_PASSPHRASE_LENGTH, BackupAuthError, BackupCorruptError, create_encrypted_backup, restore_from_backup,
    verify_backup,
)
from core.memory_manager import MemoryManager
from core.memory_privacy import (
    CONFIRM_PHRASE, MemoryPrivacyDashboard, RetentionPolicy, apply_retention, policy_for_profile,
)

PASSPHRASE = "una frase lunga e segreta"
FAST = {"scrypt_n": 2**12}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.memory = MemoryManager(self.dir / "memory.db")
        self.addCleanup(self.memory.close)
        self.dash = MemoryPrivacyDashboard(self.memory, receipts_path=self.dir / "receipts.jsonl")


class SearchAndExplainTests(Base):
    def setUp(self):
        super().setUp()
        self.memory.remember("compleanno", "5 marzo", category="fact", sensitivity="personal", created_by="davide")
        self.memory.remember("wifi casa", "hunter2-XYZ", category="fact", sensitivity="secret", source="inferred")
        self.memory.remember("caffe", "senza zucchero", category="preference", sensitivity="public")

    def test_filters_combine_and_explain_why_each_record_matched(self):
        found = self.dash.search(category="fact", sensitivity="personal")
        self.assertEqual([r.key for r in found], ["compleanno"])
        self.assertIn("sensibilita' = personal", found[0].why)
        self.assertIn("categoria = fact", found[0].why)

    def test_search_by_text_source_and_owner(self):
        self.assertEqual([r.key for r in self.dash.search("zucchero")], ["caffe"])
        self.assertEqual([r.key for r in self.dash.search(source="inferred")], ["wifi casa"])
        self.memory.remember("x", "y", owner="anna")
        self.assertEqual([r.key for r in self.dash.search(owner="anna")], ["x"])

    def test_an_unknown_sensitivity_filter_is_an_error(self):
        with self.assertRaises(ValueError):
            self.dash.search(sensitivity="boh")

    def test_expired_memories_are_hidden_unless_asked(self):
        self.memory.remember("vecchio", "dato", ttl_days=-1)
        self.assertNotIn("vecchio", [r.key for r in self.dash.search()])
        found = [r for r in self.dash.search(include_expired=True) if r.key == "vecchio"]
        self.assertTrue(found and found[0].expired)

    def test_explain_says_who_created_it_where_it_comes_from_and_how_it_lives(self):
        text = self.dash.explain(self.dash.get("compleanno"))
        for fragment in ("compleanno", "davide", "detto esplicitamente dall'utente", "personal", "Non ha una scadenza", "mai stato usato"):
            self.assertIn(fragment, text)
        inferred = self.dash.explain(self.dash.get("wifi casa"))
        self.assertIn("dedotto da Jake", inferred)
        self.assertIn("secret", inferred)

    def test_explain_mentions_pin_use_expiry_and_embedding(self):
        self.memory.remember("k", "v", embedding=[0.1, 0.2], ttl_days=5)
        self.dash.pin("k")
        self.dash.record_use("k", context="risposta a una domanda")
        text = self.dash.explain(self.dash.get("k"))
        for fragment in ("fissato", "Usato 1 volte", "Scade il", "embedding"):
            self.assertIn(fragment, text)


class EditAndAuditTests(Base):
    def test_editing_the_text_clears_the_stale_embedding_and_records_which_fields_changed_not_the_values(self):
        self.memory.remember("k", "vecchio testo", embedding=[0.5, 0.5])
        self.assertTrue(self.dash.get("k").has_embedding)
        self.assertTrue(self.dash.edit("k", value="nuovo testo", sensitivity="personal"))
        record = self.dash.get("k")
        self.assertEqual((record.value, record.sensitivity, record.has_embedding), ("nuovo testo", "personal", False))
        trail = self.dash.audit_trail("k")
        edited = [e for e in trail if e["event"] == "edited"]
        self.assertEqual(edited[0]["detail"], "value, sensitivity")
        self.assertNotIn("nuovo testo", json.dumps(trail))  # il registro non duplica il contenuto

    def test_editing_a_field_other_than_the_text_keeps_the_embedding(self):
        self.memory.remember("k", "testo", embedding=[0.5, 0.5])
        self.dash.edit("k", importance=5)
        self.assertTrue(self.dash.get("k").has_embedding)

    def test_edit_of_nothing_or_of_a_missing_memory(self):
        self.memory.remember("k", "v")
        self.assertFalse(self.dash.edit("k"))
        self.assertFalse(self.dash.edit("inesistente", value="x"))
        with self.assertRaises(ValueError):
            self.dash.edit("k", sensitivity="boh")

    def test_pin_unpin_expire_and_use(self):
        self.memory.remember("k", "v")
        self.assertTrue(self.dash.pin("k"))
        self.assertTrue(self.dash.get("k").pinned)
        self.assertTrue(self.dash.pin("k", pinned=False))
        self.assertFalse(self.dash.get("k").pinned)
        self.assertTrue(self.dash.expire("k"))
        self.assertEqual(self.memory.recall(key="k"), [])  # sparisce dalle risposte...
        self.assertIsNotNone(self.dash.get("k"))  # ...ma resta recuperabile finche' non si cancella
        self.dash.record_use("k")
        self.dash.record_use("k")
        record = self.dash.get("k")
        self.assertEqual(record.use_count, 2)
        self.assertIsNotNone(record.last_used_at)
        events = [e["event"] for e in self.dash.audit_trail("k")]
        self.assertEqual(events, ["created", "pinned", "unpinned", "expired", "used", "used"])

    def test_actions_on_a_missing_memory_return_false_and_leave_no_audit(self):
        self.assertFalse(self.dash.pin("x"))
        self.assertFalse(self.dash.expire("x"))
        self.assertFalse(self.dash.record_use("x"))
        self.assertEqual(self.dash.audit_trail("x"), [])

    def test_unlink_removes_relations_in_both_directions_only(self):
        for key in ("a", "b", "c"):
            self.memory.remember(key, key)
        self.memory.link("a", "fact", "conosce", "b", "fact")
        self.memory.link("c", "fact", "conosce", "a", "fact")
        self.memory.link("b", "fact", "conosce", "c", "fact")
        self.assertEqual(self.dash.unlink("a"), 2)
        self.assertEqual(self.memory.related("b", "fact", "conosce")[0]["key"], "c")  # la b-c e' intatta
        self.assertIsNotNone(self.dash.get("a"))  # il ricordo resta

    def test_unlink_can_be_limited_to_one_predicate(self):
        for key in ("a", "b"):
            self.memory.remember(key, key)
        self.memory.link("a", "fact", "conosce", "b", "fact")
        self.memory.link("a", "fact", "lavora con", "b", "fact")
        self.assertEqual(self.dash.unlink("a", predicate="conosce"), 1)


class DeletionTests(Base):
    def test_delete_removes_the_memory_its_relations_and_its_audit_and_returns_a_receipt_without_the_content(self):
        self.memory.remember("password wifi", "hunter2-PAROLA-UNICA", sensitivity="secret")
        self.memory.remember("router", "fritz")
        self.memory.link("password wifi", "fact", "di", "router", "fact")
        receipt = self.dash.delete("password wifi", actor="davide")
        assert receipt is not None
        self.assertEqual(receipt.rows_removed, {"memory_relations": 1, "memory_audit": 1, "memories": 1})
        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.residue_check, "clean")
        self.assertIsNone(self.dash.get("password wifi"))
        self.assertEqual(self.dash.audit_trail("password wifi"), [])
        dumped = json.dumps(receipt.to_dict())
        self.assertNotIn("hunter2", dumped)
        self.assertNotIn("password wifi", dumped)  # solo un hash della chiave
        self.assertEqual(len(receipt.memory_hash), 64)

    def test_the_deleted_value_is_really_gone_from_the_database_file(self):
        """secure_delete: senza, il contenuto di una riga cancellata resta nelle pagine libere del file."""
        secret = "VALORE-DA-CANCELLARE-DAVVERO-9f3a1c"
        self.memory.remember("k", secret)
        self.memory.connection.commit()
        self.assertIn(secret.encode(), (self.dir / "memory.db").read_bytes())  # c'era
        self.dash.delete("k")
        self.assertNotIn(secret.encode(), (self.dir / "memory.db").read_bytes())  # non c'e' piu'

    def test_a_derived_index_is_cleaned_and_reported_per_index(self):
        index = {("k", "fact")}
        self.dash.register_index("nest", remove=lambda k, c: index.discard((k, c)), contains=lambda k, c: (k, c) in index)
        self.memory.remember("k", "v")
        receipt = self.dash.delete("k")
        assert receipt is not None
        self.assertEqual(receipt.derived_indexes, {"nest": {"removed": True, "still_present": False}})
        self.assertTrue(receipt.verified)

    def test_an_index_that_still_holds_the_memory_makes_the_receipt_unverified(self):
        self.dash.register_index("rotto", remove=lambda k, c: None, contains=lambda k, c: True)
        self.memory.remember("k", "v")
        receipt = self.dash.delete("k")
        assert receipt is not None
        self.assertFalse(receipt.verified)
        self.assertTrue(receipt.derived_indexes["rotto"]["still_present"])

    def test_an_index_that_raises_is_reported_not_swallowed(self):
        def boom(k, c):
            raise RuntimeError("indice offline")

        self.dash.register_index("nest", remove=boom, contains=lambda k, c: False)
        self.memory.remember("k", "v")
        receipt = self.dash.delete("k")
        assert receipt is not None
        self.assertFalse(receipt.verified)
        self.assertEqual(receipt.derived_indexes["nest"]["error"], "RuntimeError")

    def test_deleting_a_missing_memory_returns_none(self):
        self.assertIsNone(self.dash.delete("mai-esistito"))

    def test_receipts_are_appended_to_the_receipts_file(self):
        self.memory.remember("k1", "v1")
        self.memory.remember("k2", "v2")
        self.dash.delete("k1")
        self.dash.delete("k2")
        lines = (self.dir / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(self.dash.receipts), 2)


class PurgeTests(Base):
    def test_purge_needs_the_exact_confirmation_phrase(self):
        self.memory.remember("k", "un valore abbastanza lungo")
        for wrong in ("", "elimina tutto", "ELIMINA", "si"):
            with self.subTest(wrong=wrong), self.assertRaises(PermissionError):
                self.dash.purge_everything(wrong)
        self.assertEqual(self.memory.count_memories(), 1)

    def test_purge_removes_everything_compacts_the_file_and_deletes_local_backups(self):
        for i in range(5):
            self.memory.remember(f"chiave{i}", f"contenuto molto personale numero {i} xyz")
        self.memory.link("chiave0", "fact", "vicino a", "chiave1", "fact")
        self.memory.log_turn("user", "una frase della cronologia da cancellare")
        from core.memory_schema import create_backup

        create_backup(self.memory.connection, self.dir / "memory.db", "manual")
        cleared = []
        self.dash.register_index("nest", remove=lambda k, c: None, contains=lambda k, c: False, clear_all=lambda: cleared.append(1))
        receipt = self.dash.purge_everything(CONFIRM_PHRASE)
        self.assertTrue(receipt["verified"], receipt)
        self.assertEqual(receipt["rows_remaining"], {"memories": 0, "memory_relations": 0, "conversation_history": 0, "memory_audit": 0})
        self.assertEqual(receipt["local_backups_removed"], 1)
        self.assertEqual(receipt["residue_check"], "clean")
        self.assertEqual(cleared, [1])
        self.assertFalse((self.dir / "backups").exists())
        data = (self.dir / "memory.db").read_bytes()
        self.assertNotIn(b"contenuto molto personale", data)
        self.assertNotIn("una frase della cronologia".encode(), data)

    def test_the_receipt_says_what_it_cannot_reach(self):
        receipt = self.dash.purge_everything(CONFIRM_PHRASE)
        joined = " ".join(receipt["not_reachable"])
        self.assertIn("copie esportate", joined)
        self.assertIn("promemoria", joined)

    def test_an_index_without_clear_all_makes_the_purge_unverified(self):
        self.dash.register_index("nest", remove=lambda k, c: None, contains=lambda k, c: False)
        receipt = self.dash.purge_everything(CONFIRM_PHRASE)
        self.assertFalse(receipt["verified"])
        self.assertFalse(receipt["derived_indexes"]["nest"]["cleared"])

    def test_purge_does_not_touch_reminders_or_todos_tables(self):
        self.memory.connection.execute("CREATE TABLE IF NOT EXISTS altra_cosa (x TEXT)")
        self.memory.connection.execute("INSERT INTO altra_cosa VALUES ('resta')")
        self.memory.connection.commit()
        self.dash.purge_everything(CONFIRM_PHRASE)
        self.assertEqual(self.memory.connection.execute("SELECT COUNT(*) FROM altra_cosa").fetchone()[0], 1)


class RetentionTests(Base):
    NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)

    def _remember_at(self, key, days_old, category="fact", sensitivity=None):
        self.memory.remember(key, f"valore {key}", category=category, sensitivity=sensitivity)
        stamp = (self.NOW - timedelta(days=days_old)).isoformat()
        self.memory.connection.execute("UPDATE memories SET updated_at = ? WHERE key = ?", (stamp, key))
        self.memory.connection.commit()

    def test_the_default_is_a_preview_that_deletes_nothing(self):
        self._remember_at("vecchio", 100)
        report = apply_retention(self.dash, RetentionPolicy(default_days=30), now=self.NOW)
        self.assertTrue(report.dry_run)
        self.assertEqual((len(report.candidates), report.deleted), (1, 0))
        self.assertIsNotNone(self.dash.get("vecchio"))

    def test_the_shortest_applicable_rule_wins_between_category_and_sensitivity(self):
        policy = RetentionPolicy(by_category={"event": 90}, by_sensitivity={"sensitive": 7})
        self.assertEqual(policy.rule_for("event", "sensitive"), (7, "sensibilita' sensitive"))
        self.assertEqual(policy.rule_for("event", "public"), (90, "categoria event"))
        self.assertEqual(policy.rule_for("fact", "public"), (None, "nessuna"))
        self.assertEqual(RetentionPolicy(default_days=365).rule_for("fact", "public"), (365, "default"))

    def test_applying_deletes_only_old_unpinned_memories_with_receipts(self):
        self._remember_at("evento vecchio", 100, category="event")
        self._remember_at("evento recente", 10, category="event")
        self._remember_at("evento fissato", 400, category="event")
        self._remember_at("fatto vecchio", 400, category="fact")
        self.dash.pin("evento fissato", "event")
        report = apply_retention(self.dash, RetentionPolicy(by_category={"event": 30}), dry_run=False, now=self.NOW)
        self.assertEqual(report.deleted, 1)
        self.assertIsNone(self.dash.get("evento vecchio", "event"))
        for key, category in (("evento recente", "event"), ("evento fissato", "event"), ("fatto vecchio", "fact")):
            self.assertIsNotNone(self.dash.get(key, category), key)
        self.assertEqual(len(self.dash.receipts), 1)

    def test_sensitive_memories_can_have_a_shorter_life_than_their_category(self):
        self._remember_at("dato sensibile", 20, sensitivity="sensitive")
        self._remember_at("dato normale", 20, sensitivity="public")
        report = apply_retention(self.dash, RetentionPolicy(by_sensitivity={"sensitive": 7}, default_days=365), dry_run=False, now=self.NOW)
        self.assertEqual(report.deleted, 1)
        self.assertIsNone(self.dash.get("dato sensibile"))
        self.assertIsNotNone(self.dash.get("dato normale"))

    def test_history_retention_is_applied_only_when_configured_and_not_in_dry_run(self):
        self.memory.log_turn("user", "vecchia frase")
        self.memory.connection.execute("UPDATE conversation_history SET created_at = ?", ((self.NOW - timedelta(days=100)).isoformat(),))
        self.memory.connection.commit()
        apply_retention(self.dash, RetentionPolicy(history_days=30), dry_run=True, now=self.NOW)
        self.assertEqual(len(self.memory.get_recent_history()), 1)
        report = apply_retention(self.dash, RetentionPolicy(history_days=30), dry_run=False, now=self.NOW)
        self.assertGreaterEqual(report.history_deleted, 1)

    def test_no_rule_means_keep_forever(self):
        self._remember_at("antichissimo", 5000)
        self.assertEqual(apply_retention(self.dash, RetentionPolicy(), dry_run=False, now=self.NOW).deleted, 0)

    def test_policy_validation(self):
        for bad in ({"by_category": {"event": 0}}, {"by_category": {"event": "30"}}, {"default_days": -5},
                    {"by_sensitivity": {"boh": 5}}, {"history_days": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                RetentionPolicy.from_dict(bad)
        policy = RetentionPolicy.from_dict({"by_category": {"event": 30}, "default_days": 365})
        self.assertEqual(RetentionPolicy.from_dict(policy.to_dict()), policy)

    def test_each_profile_has_its_own_policy_and_a_guest_has_none(self):
        owner = SimpleNamespace(persistent=True, preferences={"retention": {"default_days": 30}})
        child = SimpleNamespace(persistent=True, preferences={"retention": {"by_category": {"event": 7}}})
        guest = SimpleNamespace(persistent=False, preferences={"retention": {"default_days": 1}})
        self.assertEqual(policy_for_profile(owner).default_days, 30)
        self.assertEqual(policy_for_profile(child).by_category, {"event": 7})
        self.assertEqual(policy_for_profile(guest), RetentionPolicy())
        self.assertEqual(policy_for_profile(SimpleNamespace(persistent=True, preferences={})), RetentionPolicy())


class ExportTests(Base):
    def setUp(self):
        super().setUp()
        self.memory.remember("compleanno", "5 marzo", sensitivity="personal", embedding=[0.1, 0.2])
        self.memory.remember("wifi", "hunter2-XYZ", sensitivity="secret")
        self.memory.remember("amico", "Marco")
        self.memory.link("compleanno", "fact", "di", "amico", "fact")
        self.memory.link("wifi", "fact", "di", "amico", "fact")

    def test_the_default_export_excludes_secrets_and_embeddings_and_says_so(self):
        payload = self.dash.export_payload()
        keys = {r["key"] for r in payload["records"]}
        self.assertEqual(keys, {"compleanno", "amico"})
        self.assertEqual(payload["excluded_secret"], 1)
        self.assertNotIn("embedding", payload["records"][0])
        self.assertEqual(len(payload["relations"]), 1)  # la relazione che toccava il segreto non c'e'
        self.assertEqual((payload["format"], payload["version"]), ("jake-memory-export", 1))

    def test_secrets_and_embeddings_are_included_only_when_asked(self):
        payload = self.dash.export_payload(include_secret=True, include_embeddings=True)
        self.assertEqual({r["key"] for r in payload["records"]}, {"compleanno", "wifi", "amico"})
        self.assertIsNotNone(next(r for r in payload["records"] if r["key"] == "compleanno")["embedding"])
        self.assertEqual(len(payload["relations"]), 2)

    def test_json_export_is_valid_machine_readable_json(self):
        path = self.dash.export_json(self.dir / "export.json")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["schema_version"], 2)
        self.assertEqual(len(loaded["records"]), 2)

    def test_markdown_export_is_readable_and_omits_secrets(self):
        text = self.dash.export_markdown(self.dir / "export.md").read_text(encoding="utf-8")
        self.assertIn("# Memoria di Jake", text)
        self.assertIn("**compleanno**: 5 marzo", text)
        self.assertNotIn("hunter2", text)
        self.assertIn("1 ricordi segreti non sono inclusi", text)


class EncryptedBackupTests(Base):
    def setUp(self):
        super().setUp()
        self.memory.remember("compleanno", "5 marzo", sensitivity="personal", embedding=[0.1, 0.2], created_by="davide")
        self.memory.remember("wifi", "hunter2-PAROLA-UNICA", sensitivity="secret", category="credential")
        self.memory.remember("amico", "Marco")
        self.dash.pin("compleanno")
        self.memory.link("compleanno", "fact", "di", "amico", "fact")
        self.memory.log_turn("user", "frase di cronologia")
        self.path = self.dir / "backup.jkbk"

    def _fresh(self):
        other = MemoryManager(self.dir / "altra.db")
        self.addCleanup(other.close)
        return other

    def test_roundtrip_restores_everything_including_secrets_embeddings_and_pins(self):
        info = create_encrypted_backup(self.memory, self.path, PASSPHRASE, include_history=True, **FAST)
        self.assertEqual((info.records, info.relations, info.history_entries), (3, 1, 1))
        target = self._fresh()
        report = restore_from_backup(self.path, PASSPHRASE, target)
        self.assertEqual((report.restored, report.relations_restored), (3, 1))
        dash = MemoryPrivacyDashboard(target)
        wifi = dash.get("wifi", "credential")
        assert wifi is not None
        self.assertEqual((wifi.value, wifi.sensitivity), ("hunter2-PAROLA-UNICA", "secret"))
        birthday = dash.get("compleanno")
        assert birthday is not None
        self.assertTrue(birthday.pinned and birthday.has_embedding)
        self.assertEqual(birthday.created_by, "davide")

    def test_the_file_contains_no_plaintext(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        blob = self.path.read_bytes()
        for needle in (b"hunter2", b"compleanno", b"5 marzo", b"Marco"):
            self.assertNotIn(needle, blob)
        self.assertTrue(blob.startswith(b"JAKEBK01"))

    def test_verify_reads_without_writing_anything(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        before = self.memory.count_memories()
        info = verify_backup(self.path, PASSPHRASE)
        self.assertEqual((info.records, info.integrity_ok), (3, True))
        self.assertEqual(self.memory.count_memories(), before)

    def test_a_wrong_passphrase_is_refused(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        with self.assertRaises(BackupAuthError):
            verify_backup(self.path, "un'altra frase sbagliata")
        with self.assertRaises(BackupAuthError):
            restore_from_backup(self.path, "un'altra frase sbagliata", self._fresh())

    def test_tampering_with_the_ciphertext_or_the_header_is_detected(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        original = self.path.read_bytes()
        for label, position in (("ciphertext", len(original) - 5), ("header", 20)):
            tampered = bytearray(original)
            tampered[position] ^= 0x01
            self.path.write_bytes(bytes(tampered))
            with self.subTest(label=label), self.assertRaises((BackupAuthError, BackupCorruptError)):
                verify_backup(self.path, PASSPHRASE)

    def test_a_truncated_or_foreign_file_is_reported_as_corrupt(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        original = self.path.read_bytes()
        self.path.write_bytes(original[:12])
        with self.assertRaises(BackupCorruptError):
            verify_backup(self.path, PASSPHRASE)
        self.path.write_bytes(b"non sono un backup" * 10)
        with self.assertRaises(BackupCorruptError):
            verify_backup(self.path, PASSPHRASE)
        self.path.write_bytes(original[:-40])  # troncato: il tag GCM non torna
        with self.assertRaises((BackupAuthError, BackupCorruptError)):
            verify_backup(self.path, PASSPHRASE)

    def test_a_hostile_header_cannot_request_huge_memory(self):
        import base64
        import struct

        header = json.dumps({"v": 1, "kdf": "scrypt", "n": 2**28, "r": 8, "p": 1,
                             "salt": base64.b64encode(b"s" * 16).decode(), "nonce": base64.b64encode(b"n" * 12).decode()}).encode()
        self.path.write_bytes(b"JAKEBK01" + struct.pack(">I", len(header)) + header + b"x" * 64)
        with self.assertRaises(BackupCorruptError):
            verify_backup(self.path, PASSPHRASE)

    def test_a_short_passphrase_is_refused(self):
        with self.assertRaises(ValueError):
            create_encrypted_backup(self.memory, self.path, "corta", **FAST)
        self.assertFalse(self.path.exists())
        self.assertGreaterEqual(MIN_PASSPHRASE_LENGTH, 10)

    def test_two_backups_of_the_same_data_differ(self):
        second = self.dir / "second.jkbk"
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        create_encrypted_backup(self.memory, second, PASSPHRASE, **FAST)
        self.assertNotEqual(self.path.read_bytes(), second.read_bytes())  # sale e nonce casuali

    def test_no_temporary_file_is_left_after_a_backup(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        self.assertEqual(sorted(p.name for p in self.dir.glob("backup*")), ["backup.jkbk"])

    def test_selective_restore_by_category_and_by_key(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        only_credentials = self._fresh()
        report = restore_from_backup(self.path, PASSPHRASE, only_credentials, categories={"credential"})
        self.assertEqual((report.restored, report.filtered_out), (1, 2))
        self.assertEqual(only_credentials.count_memories(), 1)
        by_key = MemoryManager(self.dir / "terza.db")
        self.addCleanup(by_key.close)
        restore_from_backup(self.path, PASSPHRASE, by_key, keys={"amico"})
        self.assertEqual([r["key"] for r in by_key.recall(limit=10)], ["amico"])

    def test_relations_are_restored_only_when_both_ends_are(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        partial = self._fresh()
        report = restore_from_backup(self.path, PASSPHRASE, partial, keys={"compleanno"})
        self.assertEqual(report.relations_restored, 0)

    def test_conflicts_are_never_overwritten_silently(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        target = self._fresh()
        target.remember("amico", "Luca")
        skip = restore_from_backup(self.path, PASSPHRASE, target)
        self.assertEqual((skip.skipped_existing, target.recall(key="amico")[0]["value"]), (1, "Luca"))
        over = restore_from_backup(self.path, PASSPHRASE, target, on_conflict="overwrite")
        self.assertEqual(over.overwritten, 3)  # la prima passata aveva gia' portato dentro gli altri due
        self.assertEqual(target.recall(key="amico")[0]["value"], "Marco")

    def test_newer_wins_keeps_the_more_recent_edit(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        target = self._fresh()
        target.remember("amico", "Luca")  # modificato DOPO il backup
        newer = restore_from_backup(self.path, PASSPHRASE, target, on_conflict="newer")
        self.assertEqual(newer.skipped_existing, 1)
        self.assertEqual(target.recall(key="amico")[0]["value"], "Luca")

    def test_an_invalid_conflict_policy_is_rejected(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        with self.assertRaises(ValueError):
            restore_from_backup(self.path, PASSPHRASE, self._fresh(), on_conflict="a caso")

    def test_restore_is_audited(self):
        create_encrypted_backup(self.memory, self.path, PASSPHRASE, **FAST)
        target = self._fresh()
        restore_from_backup(self.path, PASSPHRASE, target)
        events = [e["event"] for e in MemoryPrivacyDashboard(target).audit_trail("amico")]
        self.assertEqual(events, ["restored"])


if __name__ == "__main__":
    unittest.main()
