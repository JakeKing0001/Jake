"""Test unitari per AuthGate (v5.5, Identity & Authentication)."""
import unittest

from core.auth_gate import AuthGate


class AuthGateTests(unittest.TestCase):
    def test_disabled_by_default(self):
        gate = AuthGate()
        self.assertFalse(gate.enabled)

    def test_disabled_with_empty_passphrase(self):
        gate = AuthGate(passphrase="   ")
        self.assertFalse(gate.enabled)

    def test_enabled_with_a_passphrase(self):
        gate = AuthGate(passphrase="apri sesamo")
        self.assertTrue(gate.enabled)

    def test_check_matches_the_exact_passphrase(self):
        gate = AuthGate(passphrase="apri sesamo")
        self.assertTrue(gate.check("apri sesamo"))

    def test_check_trims_whitespace(self):
        gate = AuthGate(passphrase="apri sesamo")
        self.assertTrue(gate.check("  apri sesamo  "))

    def test_check_rejects_a_wrong_attempt(self):
        gate = AuthGate(passphrase="apri sesamo")
        self.assertFalse(gate.check("chiuditi sesamo"))

    def test_check_always_false_when_disabled(self):
        gate = AuthGate()
        self.assertFalse(gate.check(""))
        self.assertFalse(gate.check("qualsiasi cosa"))


if __name__ == "__main__":
    unittest.main()
