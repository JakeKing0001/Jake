"""Test unitari per l'action ledger (F1, Trustworthy Agent Core 3.0 - vedi core/action_ledger.py
e la fase F1 in ROADMAP.md): a differenza di core/logger.log_action (F0), qui si verifica che
non ruoti mai e che authorization_of() derivi correttamente lo stato di autorizzazione dagli
stessi segnali gia' usati altrove, invece di essere dichiarato a mano da chi registra."""
import tempfile
import unittest
from pathlib import Path

from core.action_ledger import (
    AUTHORIZATION_BLOCKED, AUTHORIZATION_CONFIRMED, AUTHORIZATION_NONE, AUTHORIZATION_PASSPHRASE,
    AUTHORIZATION_PENDING, AUTHORIZATION_WINDOWS_HELLO, ActionLedger, ActionReceipt, authorization_of,
    idempotency_key_of, new_action_id,
)


class NewActionIdTests(unittest.TestCase):
    def test_returns_a_non_empty_hex_string(self):
        action_id = new_action_id()
        self.assertTrue(action_id)
        int(action_id, 16)

    def test_two_calls_do_not_collide(self):
        self.assertNotEqual(new_action_id(), new_action_id())


class AuthorizationOfTests(unittest.TestCase):
    def test_no_authorization_needed_by_default(self):
        self.assertEqual(authorization_of("success", {}), AUTHORIZATION_NONE)

    def test_confirmed_parameter_means_confirmed(self):
        self.assertEqual(authorization_of("success", {"confirmed": True}), AUTHORIZATION_CONFIRMED)

    def test_authenticated_parameter_means_passphrase(self):
        """Senza authenticated_via (ricevute scritte prima che questo campo esistesse), il
        default resta "passphrase" per compatibilita' con quelle vecchie."""
        self.assertEqual(authorization_of("success", {"authenticated": True}), AUTHORIZATION_PASSPHRASE)

    def test_authenticated_via_windows_hello_is_distinguished_from_passphrase(self):
        self.assertEqual(
            authorization_of("success", {"authenticated": True, "authenticated_via": "windows_hello"}),
            AUTHORIZATION_WINDOWS_HELLO,
        )

    def test_authenticated_via_passphrase_is_explicit_passphrase(self):
        self.assertEqual(
            authorization_of("success", {"authenticated": True, "authenticated_via": "passphrase"}),
            AUTHORIZATION_PASSPHRASE,
        )

    def test_authenticated_wins_over_confirmed_when_both_present(self):
        """Un'azione ADMIN autenticata con passphrase ha anche 'confirmed' storicamente
        propagato da JakeCore._resolve_and_execute: passphrase e' il fattore piu' forte, deve
        vincere lei, non essere degradata a una semplice conferma si'/no."""
        self.assertEqual(
            authorization_of("success", {"confirmed": True, "authenticated": True}), AUTHORIZATION_PASSPHRASE,
        )

    def test_confirmation_required_is_pending_not_none(self):
        self.assertEqual(authorization_of("confirmation_required", {}), AUTHORIZATION_PENDING)
        self.assertEqual(authorization_of("auth_required", {}), AUTHORIZATION_PENDING)

    def test_blocked_by_policy_is_blocked(self):
        self.assertEqual(authorization_of("blocked_by_policy", {}), AUTHORIZATION_BLOCKED)
        self.assertEqual(authorization_of("policy_blocked", {}), AUTHORIZATION_BLOCKED)

    def test_none_parameters_does_not_crash(self):
        self.assertEqual(authorization_of("success", None), AUTHORIZATION_NONE)


class IdempotencyKeyOfTests(unittest.TestCase):
    def test_same_intent_and_parameters_produce_the_same_key(self):
        key1 = idempotency_key_of("OPEN_APP", {"name": "spotify"})
        key2 = idempotency_key_of("OPEN_APP", {"name": "spotify"})
        self.assertEqual(key1, key2)

    def test_parameter_order_does_not_matter(self):
        key1 = idempotency_key_of("CREATE_PATH", {"path": "x", "confirmed": True})
        key2 = idempotency_key_of("CREATE_PATH", {"confirmed": True, "path": "x"})
        self.assertEqual(key1, key2)

    def test_different_parameters_produce_different_keys(self):
        key1 = idempotency_key_of("OPEN_APP", {"name": "spotify"})
        key2 = idempotency_key_of("OPEN_APP", {"name": "discord"})
        self.assertNotEqual(key1, key2)

    def test_different_intents_produce_different_keys_even_with_the_same_parameters(self):
        key1 = idempotency_key_of("OPEN_APP", {"name": "x"})
        key2 = idempotency_key_of("CLOSE_APP", {"name": "x"})
        self.assertNotEqual(key1, key2)

    def test_none_parameters_does_not_crash(self):
        self.assertTrue(idempotency_key_of("HELP", None))


class ActionReceiptTests(unittest.TestCase):
    def test_to_json_omits_none_fields(self):
        receipt = ActionReceipt(
            action_id="a1", trace_id="t1", ts=123.0, intent="OPEN_APP", requested_by="user",
            risk_decision="local_reversible", authorization="none", result="success",
        )
        import json
        record = json.loads(receipt.to_json())
        self.assertNotIn("verified", record)
        self.assertNotIn("duration_ms", record)
        self.assertNotIn("model", record)
        self.assertEqual(record["action_id"], "a1")


class ActionLedgerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "ledger.jsonl"
        self.ledger = ActionLedger(path=self.path)

    def _receipt(self, **overrides) -> ActionReceipt:
        defaults = dict(
            action_id="a1", trace_id="t1", ts=123.0, intent="OPEN_APP", requested_by="user",
            risk_decision="local_reversible", authorization="none", result="success",
        )
        defaults.update(overrides)
        return ActionReceipt(**defaults)


class RecordTests(ActionLedgerTestCase):
    def test_record_appends_a_json_line(self):
        self.ledger.record(self._receipt())
        records = self.ledger.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["intent"], "OPEN_APP")

    def test_record_appends_without_truncating_previous_entries(self):
        """Diverso da un log che ruota (core/logger.py): il ledger non deve mai perdere voci
        vecchie scrivendone di nuove."""
        for i in range(5):
            self.ledger.record(self._receipt(action_id=f"a{i}"))
        records = self.ledger.read_all()
        self.assertEqual(len(records), 5)
        self.assertEqual([r["action_id"] for r in records], [f"a{i}" for i in range(5)])

    def test_private_flag_suppresses_recording(self):
        self.ledger.record(self._receipt(), private=True)
        self.assertEqual(self.ledger.read_all(), [])


class QueryTests(ActionLedgerTestCase):
    def test_by_trace_id_filters_correctly(self):
        self.ledger.record(self._receipt(action_id="a1", trace_id="shared"))
        self.ledger.record(self._receipt(action_id="a2", trace_id="shared"))
        self.ledger.record(self._receipt(action_id="a3", trace_id="other"))

        results = self.ledger.by_trace_id("shared")

        self.assertEqual({r["action_id"] for r in results}, {"a1", "a2"})

    def test_by_action_id_returns_the_matching_record_or_none(self):
        self.ledger.record(self._receipt(action_id="a1"))

        self.assertEqual(self.ledger.by_action_id("a1")["action_id"], "a1")
        self.assertIsNone(self.ledger.by_action_id("does-not-exist"))

    def test_by_idempotency_key_filters_correctly(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key="k1"))
        self.ledger.record(self._receipt(action_id="a2", idempotency_key="k1"))
        self.ledger.record(self._receipt(action_id="a3", idempotency_key="k2"))

        results = self.ledger.by_idempotency_key("k1")

        self.assertEqual({r["action_id"] for r in results}, {"a1", "a2"})


class DuplicateIdempotencyKeysTests(ActionLedgerTestCase):
    def test_two_receipts_with_the_same_key_close_in_time_are_flagged(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key="k1", ts=100.0))
        self.ledger.record(self._receipt(action_id="a2", idempotency_key="k1", ts=101.0))

        duplicates = self.ledger.duplicate_idempotency_keys(within_seconds=60)

        self.assertIn("k1", duplicates)
        self.assertEqual({r["action_id"] for r in duplicates["k1"]}, {"a1", "a2"})

    def test_same_key_far_apart_in_time_is_not_flagged(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key="k1", ts=100.0))
        self.ledger.record(self._receipt(action_id="a2", idempotency_key="k1", ts=10000.0))

        duplicates = self.ledger.duplicate_idempotency_keys(within_seconds=60)

        self.assertNotIn("k1", duplicates)

    def test_single_occurrence_is_not_a_duplicate(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key="k1", ts=100.0))

        self.assertEqual(self.ledger.duplicate_idempotency_keys(), {})

    def test_receipts_without_an_idempotency_key_are_ignored(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key=None, ts=100.0))
        self.ledger.record(self._receipt(action_id="a2", idempotency_key=None, ts=101.0))

        self.assertEqual(self.ledger.duplicate_idempotency_keys(), {})


if __name__ == "__main__":
    unittest.main()
