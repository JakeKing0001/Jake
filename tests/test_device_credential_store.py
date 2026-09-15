"""Test unitari per core/device_credential_store.py (F1.4.5/F1.4.6, fase 2/10 del piano
multi-device). SecretsVault VERO (DPAPI reale, non mockato) - stesso principio gia' seguito in
tests/test_secrets_vault.py: solo cosi' si prova che il round trip protect()/unprotect() usato
per i token funzioni davvero, non solo che il codice chiami le funzioni giuste."""
import tempfile
import unittest
from pathlib import Path

from core.device_credential_store import DeviceCredentialStore
from core.device_identity import DeviceStatus


class _WithStore(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_device_credential_store_test_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_dir, ignore_errors=True))
        self.store = DeviceCredentialStore(db_path=tmp_dir / "devices.db")
        self.addCleanup(self.store.close)


class RegisterDeviceTests(_WithStore):
    def test_a_new_device_starts_pairing_required(self):
        identity = self.store.register_device("d1", "Telefono")
        self.assertEqual(identity.status, DeviceStatus.PAIRING_REQUIRED.value)
        self.assertEqual(identity.name, "Telefono")

    def test_registering_the_same_device_twice_does_not_lose_its_state(self):
        self.store.register_device("d1", "Telefono")
        self.store.issue_credential("d1")

        self.store.register_device("d1", "Telefono")  # secondo register(), stesso nome

        identity = self.store.get_device("d1")
        self.assertEqual(identity.status, DeviceStatus.ACTIVE.value)

    def test_a_later_register_with_a_new_name_updates_it(self):
        self.store.register_device("d1", "Vecchio nome")
        self.store.register_device("d1", "Nuovo nome")
        self.assertEqual(self.store.get_device("d1").name, "Nuovo nome")

    def test_a_later_register_with_no_name_keeps_the_known_one(self):
        self.store.register_device("d1", "Telefono")
        self.store.register_device("d1", "")
        self.assertEqual(self.store.get_device("d1").name, "Telefono")

    def test_unknown_device_is_none(self):
        self.assertIsNone(self.store.get_device("mai-visto"))

    def test_list_devices_returns_every_known_device(self):
        self.store.register_device("d1", "Uno")
        self.store.register_device("d2", "Due")
        ids = {identity.device_id for identity in self.store.list_devices()}
        self.assertEqual(ids, {"d1", "d2"})

    def test_touch_last_seen_updates_the_timestamp(self):
        self.store.register_device("d1")
        self.assertIsNone(self.store.get_device("d1").last_seen_at)
        self.store.touch_last_seen("d1")
        self.assertIsNotNone(self.store.get_device("d1").last_seen_at)


class IssueCredentialTests(_WithStore):
    def test_issuing_a_credential_activates_the_device(self):
        self.store.issue_credential("d1")
        self.assertEqual(self.store.get_device("d1").status, DeviceStatus.ACTIVE.value)

    def test_issuing_registers_a_previously_unknown_device(self):
        """issue_credential() su un device_id mai visto lo crea al volo - il primo pairing reale
        (fase 4) parte da qui: device_id nuovo, credenziale emessa subito dopo l'approvazione."""
        self.assertIsNone(self.store.get_device("d1"))
        self.store.issue_credential("d1")
        self.assertIsNotNone(self.store.get_device("d1"))

    def test_the_returned_credential_verifies_successfully(self):
        credential = self.store.issue_credential("d1")
        self.assertEqual(self.store.verify_token(credential.token), "d1")

    def test_two_devices_get_different_tokens(self):
        c1 = self.store.issue_credential("d1")
        c2 = self.store.issue_credential("d2")
        self.assertNotEqual(c1.token, c2.token)

    def test_the_token_is_never_stored_in_plaintext_on_disk(self):
        credential = self.store.issue_credential("d1")
        raw_db_contents = self.store.db_path.read_bytes()
        self.assertNotIn(credential.token.encode("utf-8"), raw_db_contents)

    def test_ttl_seconds_controls_the_expiry(self):
        fixed_now = 1_000_000.0
        store = DeviceCredentialStore(db_path=self.store.db_path.with_name("ttl.db"), time_source=lambda: fixed_now)
        self.addCleanup(store.close)
        credential = store.issue_credential("d1", ttl_seconds=60.0)
        self.assertEqual(credential.expires_at, fixed_now + 60.0)


class RotateCredentialTests(_WithStore):
    def test_rotating_an_unknown_device_returns_none(self):
        self.assertIsNone(self.store.rotate_credential("mai-registrato"))

    def test_rotating_replaces_the_token(self):
        first = self.store.issue_credential("d1")
        second = self.store.rotate_credential("d1")
        self.assertNotEqual(first.token, second.token)

    def test_the_old_token_stops_working_after_rotation(self):
        first = self.store.issue_credential("d1")
        self.store.rotate_credential("d1")
        self.assertIsNone(self.store.verify_token(first.token))

    def test_the_new_token_works_after_rotation(self):
        self.store.issue_credential("d1")
        second = self.store.rotate_credential("d1")
        self.assertEqual(self.store.verify_token(second.token), "d1")

    def test_rotating_one_device_does_not_affect_another(self):
        """F1.4.6 ('ruotato SENZA revocare gli altri')."""
        c1 = self.store.issue_credential("d1")
        self.store.issue_credential("d2")
        self.store.rotate_credential("d2")

        self.assertEqual(self.store.verify_token(c1.token), "d1")

    def test_the_device_stays_active_after_rotation_not_pairing_required(self):
        self.store.issue_credential("d1")
        self.store.rotate_credential("d1")
        self.assertEqual(self.store.get_device("d1").status, DeviceStatus.ACTIVE.value)


class RevokeTests(_WithStore):
    def test_revoking_an_unknown_device_returns_false(self):
        self.assertFalse(self.store.revoke("mai-registrato"))

    def test_revoking_a_device_without_a_credential_returns_false(self):
        self.store.register_device("d1")
        self.assertFalse(self.store.revoke("d1"))

    def test_revoking_returns_true_the_first_time(self):
        self.store.issue_credential("d1")
        self.assertTrue(self.store.revoke("d1"))

    def test_revoking_twice_returns_false_the_second_time(self):
        self.store.issue_credential("d1")
        self.store.revoke("d1")
        self.assertFalse(self.store.revoke("d1"))

    def test_a_revoked_token_no_longer_verifies(self):
        credential = self.store.issue_credential("d1")
        self.store.revoke("d1")
        self.assertIsNone(self.store.verify_token(credential.token))

    def test_revoking_returns_the_device_to_pairing_required(self):
        """F1.4.6 ('se il token... viene revocato, il dispositivo deve tornare nello stato
        PAIRING_REQUIRED') - non un quarto stato 'revoked' visibile al dispositivo."""
        self.store.issue_credential("d1")
        self.store.revoke("d1")
        self.assertEqual(self.store.get_device("d1").status, DeviceStatus.PAIRING_REQUIRED.value)

    def test_revoking_one_device_does_not_affect_another(self):
        self.store.issue_credential("d1")
        c2 = self.store.issue_credential("d2")
        self.store.revoke("d1")

        self.assertEqual(self.store.verify_token(c2.token), "d2")
        self.assertEqual(self.store.get_device("d2").status, DeviceStatus.ACTIVE.value)


class VerifyTokenTests(_WithStore):
    def test_an_unknown_token_does_not_verify(self):
        self.assertIsNone(self.store.verify_token("token-mai-emesso"))

    def test_empty_token_does_not_verify(self):
        self.assertIsNone(self.store.verify_token(""))

    def test_an_expired_token_does_not_verify(self):
        fixed_now = [1_000_000.0]
        store = DeviceCredentialStore(
            db_path=self.store.db_path.with_name("expiry.db"), time_source=lambda: fixed_now[0],
        )
        self.addCleanup(store.close)
        credential = store.issue_credential("d1", ttl_seconds=60.0)

        fixed_now[0] += 61.0  # oltre la scadenza

        self.assertIsNone(store.verify_token(credential.token))

    def test_an_unmatched_token_never_touches_an_unrelated_devices_status(self):
        fixed_now = [1_000_000.0]
        store = DeviceCredentialStore(
            db_path=self.store.db_path.with_name("expiry2.db"), time_source=lambda: fixed_now[0],
        )
        self.addCleanup(store.close)
        store.issue_credential("d1", ttl_seconds=60.0)
        fixed_now[0] += 61.0  # la credenziale di d1 e' ormai scaduta, ma non viene mai presentata

        store.verify_token("qualunque-cosa-non-corrisponde-a-nessun-token")

        self.assertEqual(
            store.get_device("d1").status, DeviceStatus.ACTIVE.value,
            "un tentativo con un token estraneo non deve far scadere un dispositivo che non c'entra",
        )

    def test_an_expired_token_moves_the_device_back_to_pairing_required(self):
        """F1.4.6 ('se il token scade... il dispositivo deve tornare nello stato
        PAIRING_REQUIRED') - riprodotto per davvero facendo scadere una credenziale vera, non
        solo letto a codice: verify_token() trova IL token vero per corrispondenza, POI scopre
        che e' scaduto - solo presentare quel token specifico fa scattare la transizione."""
        fixed_now = [1_000_000.0]
        store = DeviceCredentialStore(
            db_path=self.store.db_path.with_name("expiry3.db"), time_source=lambda: fixed_now[0],
        )
        self.addCleanup(store.close)
        credential = store.issue_credential("d1", ttl_seconds=60.0)
        fixed_now[0] += 61.0

        store.verify_token(credential.token)

        self.assertEqual(store.get_device("d1").status, DeviceStatus.PAIRING_REQUIRED.value)

    def test_a_valid_unexpired_token_verifies(self):
        fixed_now = [1_000_000.0]
        store = DeviceCredentialStore(
            db_path=self.store.db_path.with_name("valid.db"), time_source=lambda: fixed_now[0],
        )
        self.addCleanup(store.close)
        credential = store.issue_credential("d1", ttl_seconds=60.0)
        fixed_now[0] += 30.0  # ancora dentro la finestra di validita'

        self.assertEqual(store.verify_token(credential.token), "d1")


class NoGlobalFallbackTests(_WithStore):
    """F1.4.6 ('non deve esistere fallback automatico a un token globale')."""

    def test_a_device_with_no_credential_at_all_never_verifies_by_accident(self):
        self.store.register_device("d1")
        self.assertIsNone(self.store.verify_token(""))
        self.assertIsNone(self.store.verify_token("qualunque-cosa"))


class PersistenceAcrossRestartsTests(unittest.TestCase):
    def test_a_device_and_its_credential_survive_reopening_the_store(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_device_credential_store_restart_test_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_dir, ignore_errors=True))
        db_path = tmp_dir / "devices.db"

        first = DeviceCredentialStore(db_path=db_path)
        credential = first.issue_credential("d1", ttl_seconds=99999.0)
        first.close()

        second = DeviceCredentialStore(db_path=db_path)
        self.addCleanup(second.close)
        self.assertEqual(second.verify_token(credential.token), "d1")


if __name__ == "__main__":
    unittest.main()
