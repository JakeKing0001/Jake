"""Test unitari per core/pairing_service.py (F1.4.5, fase 4/10 del piano multi-device). Usa un
vero DeviceCredentialStore (SQLite temporaneo, DPAPI reale) - non un doppio: solo cosi' un
"approve()" che sembra corretto letto a codice ma non crea davvero un dispositivo verificabile
verrebbe scoperto."""
import shutil
import tempfile
import unittest
from pathlib import Path

from core.companion_guard import _ID_RE
from core.device_credential_store import DeviceCredentialStore
from core.pairing_service import PairingService


class _WithService(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_pairing_service_test_"))
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        self.store = DeviceCredentialStore(db_path=tmp_dir / "devices.db")
        self.addCleanup(self.store.close)
        self.service = PairingService(self.store)


class StartPairingTests(_WithService):
    def test_a_fresh_challenge_is_usable(self):
        challenge = self.service.start_pairing()
        self.assertTrue(challenge.is_usable())

    def test_two_calls_produce_different_challenge_ids(self):
        c1 = self.service.start_pairing()
        c2 = self.service.start_pairing()
        self.assertNotEqual(c1.challenge_id, c2.challenge_id)

    def test_expiry_is_five_minutes_out(self):
        challenge = self.service.start_pairing()
        self.assertAlmostEqual(challenge.expires_at - challenge.created_at, 5 * 60, delta=1)

    def test_every_generated_challenge_id_matches_the_servers_own_path_identifier_pattern(self):
        """Bug reale (produzione, non solo teorico): `GET /pairing/<challenge_id>`
        (`core/companion_server.py`) valida il segmento di percorso con
        `core/companion_guard.py::_ID_RE`, che pretende un PRIMO carattere alfanumerico -
        `secrets.token_urlsafe` (usato qui prima della correzione) puo' invece iniziare con `-`
        o `_`, entrambi nel suo alfabeto base64url. Un id auto-generato da Jake stesso finiva
        cosi' occasionalmente rifiutato dal suo stesso validatore (400 `invalid_challenge_id`),
        pur essendo appena stato restituito da `start_pairing()` - riprodotto ~1 volta su 32 in
        isolamento completo, non un problema di carico. Molte iterazioni: la probabilita' di NON
        pescare mai il carattere iniziale sbagliato per puro caso renderebbe un test a poche
        iterazioni inutile a prevenire una regressione futura."""
        for _ in range(2000):
            challenge = self.service.start_pairing()
            self.assertRegex(challenge.challenge_id, _ID_RE)


class QrPayloadTests(_WithService):
    def test_payload_contains_only_the_challenge_id_and_expiry(self):
        challenge = self.service.start_pairing(requested_name="Il mio telefono")
        payload = self.service.qr_payload(challenge)
        self.assertEqual(set(payload), {"challenge_id", "expires_at"})

    def test_payload_never_contains_a_token_or_any_secret(self):
        """F1.4.5 ('il payload contiene solo dati non sensibili necessari al pairing')."""
        challenge = self.service.start_pairing()
        payload = self.service.qr_payload(challenge)
        self.assertNotIn("token", payload)
        self.assertNotIn("credential", payload)


class ApprovePairingTests(_WithService):
    def test_approving_a_fresh_challenge_returns_a_credential(self):
        challenge = self.service.start_pairing()
        credential = self.service.approve(challenge.challenge_id)
        self.assertIsNotNone(credential)

    def test_approving_registers_a_real_usable_device(self):
        challenge = self.service.start_pairing()
        credential = self.service.approve(challenge.challenge_id)
        self.assertEqual(self.store.verify_token(credential.token), credential.device_id)

    def test_approving_an_unknown_challenge_returns_none(self):
        self.assertIsNone(self.service.approve("challenge-mai-esistita"))

    def test_approving_an_unknown_challenge_creates_no_device(self):
        self.service.approve("challenge-mai-esistita")
        self.assertEqual(self.store.list_devices(), [])

    def test_approving_the_same_challenge_twice_only_creates_one_device(self):
        """F1.4.5 ('replay della challenge deve fallire')."""
        challenge = self.service.start_pairing()
        first = self.service.approve(challenge.challenge_id)
        second = self.service.approve(challenge.challenge_id)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(self.store.list_devices()), 1)

    def test_approving_an_expired_challenge_returns_none(self):
        fixed_now = [1_000_000.0]
        service = PairingService(self.store, time_source=lambda: fixed_now[0])
        challenge = service.start_pairing()
        fixed_now[0] += 5 * 60 + 1

        self.assertIsNone(service.approve(challenge.challenge_id))

    def test_approving_an_expired_challenge_creates_no_device(self):
        fixed_now = [1_000_000.0]
        service = PairingService(self.store, time_source=lambda: fixed_now[0])
        challenge = service.start_pairing()
        fixed_now[0] += 5 * 60 + 1

        service.approve(challenge.challenge_id)

        self.assertEqual(self.store.list_devices(), [])

    def test_device_name_argument_is_used_when_given(self):
        challenge = self.service.start_pairing(requested_name="Nome dalla challenge")
        credential = self.service.approve(challenge.challenge_id, device_name="Nome esplicito")
        self.assertEqual(self.store.get_device(credential.device_id).name, "Nome esplicito")

    def test_requested_name_is_used_when_no_explicit_name_given(self):
        challenge = self.service.start_pairing(requested_name="Nome dalla challenge")
        credential = self.service.approve(challenge.challenge_id)
        self.assertEqual(self.store.get_device(credential.device_id).name, "Nome dalla challenge")

    def test_two_approved_pairings_get_two_different_device_ids(self):
        c1 = self.service.start_pairing()
        c2 = self.service.start_pairing()
        first = self.service.approve(c1.challenge_id)
        second = self.service.approve(c2.challenge_id)
        self.assertNotEqual(first.device_id, second.device_id)


class RejectPairingTests(_WithService):
    def test_rejecting_a_fresh_challenge_returns_true(self):
        challenge = self.service.start_pairing()
        self.assertTrue(self.service.reject(challenge.challenge_id))

    def test_rejecting_creates_no_device(self):
        """F1.4.5 ('un pairing rifiutato non deve creare alcun device')."""
        challenge = self.service.start_pairing()
        self.service.reject(challenge.challenge_id)
        self.assertEqual(self.store.list_devices(), [])

    def test_a_rejected_challenge_cannot_then_be_approved(self):
        challenge = self.service.start_pairing()
        self.service.reject(challenge.challenge_id)
        self.assertIsNone(self.service.approve(challenge.challenge_id))

    def test_rejecting_an_unknown_challenge_returns_false(self):
        self.assertFalse(self.service.reject("challenge-mai-esistita"))

    def test_rejecting_twice_returns_false_the_second_time(self):
        challenge = self.service.start_pairing()
        self.service.reject(challenge.challenge_id)
        self.assertFalse(self.service.reject(challenge.challenge_id))


class GetChallengeTests(_WithService):
    def test_returns_the_challenge_for_a_known_id(self):
        challenge = self.service.start_pairing()
        self.assertEqual(self.service.get_challenge(challenge.challenge_id).challenge_id, challenge.challenge_id)

    def test_returns_none_for_an_unknown_id(self):
        self.assertIsNone(self.service.get_challenge("mai-esistita"))


if __name__ == "__main__":
    unittest.main()
