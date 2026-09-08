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


class WindowsHelloTests(unittest.TestCase):
    """F1: windows_hello_verify e' SEMPRE iniettato qui - vedi core/windows_hello.py sul perche'
    un test non deve mai poter chiamare l'API vera di Windows Hello (mostra un prompt reale e
    aspetta l'utente, scoperto a proprie spese durante lo sviluppo di questo modulo)."""

    def test_enabled_true_when_only_windows_hello_is_on(self):
        gate = AuthGate(windows_hello_enabled=True, windows_hello_verify=lambda reason: True)
        self.assertTrue(gate.enabled)

    def test_verify_returns_false_immediately_when_disabled_without_calling_anything(self):
        calls = []
        gate = AuthGate(windows_hello_enabled=False, windows_hello_verify=lambda reason: calls.append(reason) or True)

        result = gate.verify_with_windows_hello("Spegni il computer")

        self.assertFalse(result)
        self.assertEqual(calls, [], "non deve chiamare la verifica se windows_hello_enabled e' spento")

    def test_verify_forwards_the_reason_and_returns_the_injected_result(self):
        gate = AuthGate(windows_hello_enabled=True, windows_hello_verify=lambda reason: reason == "Spegni il computer")

        self.assertTrue(gate.verify_with_windows_hello("Spegni il computer"))
        self.assertFalse(gate.verify_with_windows_hello("Un'altra richiesta"))

    def test_both_factors_can_be_enabled_together(self):
        gate = AuthGate(
            passphrase="apri sesamo", windows_hello_enabled=True, windows_hello_verify=lambda reason: True,
        )
        self.assertTrue(gate.enabled)
        self.assertTrue(gate.check("apri sesamo"))
        self.assertTrue(gate.verify_with_windows_hello("qualunque cosa"))


if __name__ == "__main__":
    unittest.main()
