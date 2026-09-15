"""Test unitari per core/device_identity.py (F1.4.4-F1.4.6/F1.8.1, multi-device): i tre
contratti dati - DeviceIdentity, DeviceCredential, PairingChallenge - nessuna suite esisteva
ancora per questo modulo, appena introdotto. Stesso stile di tests/test_action_contracts.py, che
questi test rispecchiano deliberatamente."""
import unittest

from core.device_identity import (
    DEVICE_STATUSES,
    DeviceCredential,
    DeviceIdentity,
    DeviceStatus,
    PairingChallenge,
    validate_device_credential,
    validate_device_identity,
    validate_pairing_challenge,
)


class DeviceStatusTests(unittest.TestCase):
    def test_exactly_three_statuses_exist(self):
        self.assertEqual(DEVICE_STATUSES, {"pairing_required", "active", "revoked"})

    def test_enum_values_match_the_frozenset(self):
        self.assertEqual({status.value for status in DeviceStatus}, DEVICE_STATUSES)


class DeviceIdentityTests(unittest.TestCase):
    def test_defaults_to_pairing_required(self):
        identity = DeviceIdentity(device_id="d1")
        self.assertEqual(identity.status, DeviceStatus.PAIRING_REQUIRED.value)

    def test_created_at_defaults_to_now(self):
        identity = DeviceIdentity(device_id="d1")
        self.assertGreater(identity.created_at, 0)

    def test_explicit_created_at_is_preserved(self):
        identity = DeviceIdentity(device_id="d1", created_at=123.0)
        self.assertEqual(identity.created_at, 123.0)

    def test_last_seen_at_defaults_to_none(self):
        identity = DeviceIdentity(device_id="d1")
        self.assertIsNone(identity.last_seen_at)

    def test_validate_accepts_a_well_formed_identity(self):
        identity = DeviceIdentity(device_id="d1", status=DeviceStatus.ACTIVE.value)
        validate_device_identity(identity)  # non deve sollevare

    def test_validate_rejects_missing_device_id(self):
        identity = DeviceIdentity(device_id="")
        with self.assertRaises(ValueError):
            validate_device_identity(identity)

    def test_validate_rejects_an_unknown_status(self):
        identity = DeviceIdentity(device_id="d1", status="not_a_real_status")
        with self.assertRaises(ValueError):
            validate_device_identity(identity)

    def test_validate_rejects_a_non_positive_created_at(self):
        identity = DeviceIdentity(device_id="d1", created_at=1.0)
        identity.created_at = 0.0  # bypassa __post_init__ per provare il caso rifiutato
        with self.assertRaises(ValueError):
            validate_device_identity(identity)


class DeviceCredentialTests(unittest.TestCase):
    def test_issued_at_defaults_to_now(self):
        credential = DeviceCredential(device_id="d1", token="t1")
        self.assertGreater(credential.issued_at, 0)

    def test_fresh_credential_is_not_revoked(self):
        credential = DeviceCredential(device_id="d1", token="t1")
        self.assertFalse(credential.is_revoked())

    def test_revoked_at_set_means_revoked(self):
        credential = DeviceCredential(device_id="d1", token="t1", revoked_at=100.0)
        self.assertTrue(credential.is_revoked())

    def test_no_expiry_means_never_expired(self):
        credential = DeviceCredential(device_id="d1", token="t1", expires_at=0.0)
        self.assertFalse(credential.is_expired(now=999999999.0))

    def test_expires_at_in_the_past_is_expired(self):
        credential = DeviceCredential(device_id="d1", token="t1", issued_at=100.0, expires_at=200.0)
        self.assertTrue(credential.is_expired(now=300.0))

    def test_expires_at_in_the_future_is_not_expired(self):
        credential = DeviceCredential(device_id="d1", token="t1", issued_at=100.0, expires_at=200.0)
        self.assertFalse(credential.is_expired(now=150.0))

    def test_is_valid_is_false_when_revoked_even_if_not_expired(self):
        credential = DeviceCredential(
            device_id="d1", token="t1", issued_at=100.0, expires_at=999999999.0, revoked_at=150.0,
        )
        self.assertFalse(credential.is_valid(now=160.0))

    def test_is_valid_is_false_when_expired_even_if_not_revoked(self):
        credential = DeviceCredential(device_id="d1", token="t1", issued_at=100.0, expires_at=200.0)
        self.assertFalse(credential.is_valid(now=300.0))

    def test_is_valid_is_true_when_neither_revoked_nor_expired(self):
        credential = DeviceCredential(device_id="d1", token="t1", issued_at=100.0, expires_at=999999999.0)
        self.assertTrue(credential.is_valid(now=150.0))

    def test_validate_accepts_a_well_formed_credential(self):
        credential = DeviceCredential(device_id="d1", token="t1", issued_at=100.0, expires_at=200.0)
        validate_device_credential(credential)  # non deve sollevare

    def test_validate_rejects_missing_device_id(self):
        credential = DeviceCredential(device_id="", token="t1", issued_at=100.0, expires_at=200.0)
        with self.assertRaises(ValueError):
            validate_device_credential(credential)

    def test_validate_rejects_missing_token(self):
        credential = DeviceCredential(device_id="d1", token="", issued_at=100.0, expires_at=200.0)
        with self.assertRaises(ValueError):
            validate_device_credential(credential)

    def test_validate_rejects_expiry_not_after_issuance(self):
        credential = DeviceCredential(device_id="d1", token="t1", issued_at=200.0, expires_at=200.0)
        with self.assertRaises(ValueError):
            validate_device_credential(credential)

    def test_validate_rejects_expiry_before_issuance(self):
        credential = DeviceCredential(device_id="d1", token="t1", issued_at=200.0, expires_at=100.0)
        with self.assertRaises(ValueError):
            validate_device_credential(credential)


class PairingChallengeTests(unittest.TestCase):
    def test_created_at_defaults_to_now(self):
        challenge = PairingChallenge(challenge_id="c1")
        self.assertGreater(challenge.created_at, 0)

    def test_fresh_challenge_is_usable(self):
        challenge = PairingChallenge(challenge_id="c1", created_at=100.0, expires_at=400.0)
        self.assertTrue(challenge.is_usable(now=150.0))

    def test_used_challenge_is_not_usable_even_if_not_expired(self):
        """F1.4.5 ('replay della challenge deve fallire'): una challenge gia' consumata non torna
        mai usabile solo perche' non e' ancora scaduta."""
        challenge = PairingChallenge(challenge_id="c1", created_at=100.0, expires_at=400.0, used=True)
        self.assertFalse(challenge.is_usable(now=150.0))

    def test_expired_challenge_is_not_usable_even_if_not_used(self):
        """F1.4.5 ('la challenge scade dopo 5 minuti')."""
        challenge = PairingChallenge(challenge_id="c1", created_at=100.0, expires_at=400.0)
        self.assertFalse(challenge.is_usable(now=500.0))

    def test_validate_accepts_a_well_formed_challenge(self):
        challenge = PairingChallenge(challenge_id="c1", created_at=100.0, expires_at=400.0)
        validate_pairing_challenge(challenge)  # non deve sollevare

    def test_validate_rejects_missing_challenge_id(self):
        challenge = PairingChallenge(challenge_id="", created_at=100.0, expires_at=400.0)
        with self.assertRaises(ValueError):
            validate_pairing_challenge(challenge)

    def test_validate_rejects_expiry_not_after_creation(self):
        challenge = PairingChallenge(challenge_id="c1", created_at=100.0, expires_at=100.0)
        with self.assertRaises(ValueError):
            validate_pairing_challenge(challenge)


if __name__ == "__main__":
    unittest.main()
