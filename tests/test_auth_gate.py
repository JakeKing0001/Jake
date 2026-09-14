"""Test unitari per AuthGate (v5.5, Identity & Authentication)."""
import unittest
from unittest import mock

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

    def test_check_uses_a_constant_time_comparison_not_a_plain_equality(self):
        """F1: check() confrontava la passphrase con '==', un canale laterale temporale per un
        segreto - lo stesso identico principio gia' applicato correttamente altrove nel progetto
        per un confronto di segreto (core/companion_server.py::_is_authorized). Verifica che il
        confronto passi DAVVERO da hmac.compare_digest (non solo che il risultato sia giusto, che
        una '==' produrrebbe comunque)."""
        gate = AuthGate(passphrase="apri sesamo")
        with mock.patch("core.auth_gate.hmac.compare_digest", return_value=True) as compare_digest:
            gate.check("qualunque cosa")
        compare_digest.assert_called_once_with("qualunque cosa", "apri sesamo")


class LockoutTests(unittest.TestCase):
    """F1.4.3: rate limiting/lockout dopo N tentativi falliti CONSECUTIVI (vedi il docstring del
    modulo per l'analisi dell'esposizione che questo chiude). time.monotonic() e' sempre mockato
    qui (mai il vero orologio): un test che dipendesse dal tempo reale per una soglia di 60s
    sarebbe lento o fragile, lo stesso principio gia' seguito altrove nel progetto per i timeout
    configurabili."""

    def _gate(self, max_failures=3, lockout_seconds=60.0):
        gate = AuthGate(passphrase="apri sesamo")
        gate.MAX_CONSECUTIVE_FAILURES = max_failures
        gate.LOCKOUT_SECONDS = lockout_seconds
        return gate

    def test_not_locked_out_before_reaching_the_threshold(self):
        gate = self._gate(max_failures=3)
        gate.check("sbagliata")
        gate.check("sbagliata")
        self.assertFalse(gate.is_locked_out())
        self.assertTrue(gate.check("apri sesamo"), "il terzo tentativo era corretto, non deve essere bloccato")

    def test_reaching_the_threshold_locks_out(self):
        gate = self._gate(max_failures=3)
        gate.check("sbagliata")
        gate.check("sbagliata")
        gate.check("sbagliata")
        self.assertTrue(gate.is_locked_out())

    def test_lockout_rejects_even_a_correct_passphrase(self):
        gate = self._gate(max_failures=3)
        gate.check("sbagliata")
        gate.check("sbagliata")
        gate.check("sbagliata")

        self.assertFalse(gate.check("apri sesamo"))

    def test_lockout_does_not_even_compare_while_active(self):
        gate = self._gate(max_failures=1)
        gate.check("sbagliata")
        self.assertTrue(gate.is_locked_out())

        with mock.patch("core.auth_gate.hmac.compare_digest") as compare_digest:
            gate.check("apri sesamo")
        compare_digest.assert_not_called()

    def test_a_success_resets_the_consecutive_failure_counter(self):
        gate = self._gate(max_failures=3)
        gate.check("sbagliata")
        gate.check("sbagliata")
        self.assertTrue(gate.check("apri sesamo"))

        gate.check("sbagliata")
        gate.check("sbagliata")
        self.assertFalse(gate.is_locked_out(), "il successo intermedio doveva azzerare il contatore")

    def test_lockout_expires_after_the_configured_duration(self):
        gate = self._gate(max_failures=1, lockout_seconds=60.0)
        with mock.patch("core.auth_gate.time.monotonic", return_value=1000.0):
            gate.check("sbagliata")
            self.assertTrue(gate.is_locked_out())
        with mock.patch("core.auth_gate.time.monotonic", return_value=1000.0 + 60.0 + 0.001):
            self.assertFalse(gate.is_locked_out())
            self.assertTrue(gate.check("apri sesamo"))

    def test_lockout_remaining_seconds_counts_down_to_zero(self):
        gate = self._gate(max_failures=1, lockout_seconds=60.0)
        with mock.patch("core.auth_gate.time.monotonic", return_value=1000.0):
            gate.check("sbagliata")
        with mock.patch("core.auth_gate.time.monotonic", return_value=1030.0):
            self.assertAlmostEqual(gate.lockout_remaining_seconds(), 30.0)
        with mock.patch("core.auth_gate.time.monotonic", return_value=2000.0):
            self.assertEqual(gate.lockout_remaining_seconds(), 0.0)

    def test_disabled_gate_never_locks_out(self):
        gate = AuthGate()
        gate.MAX_CONSECUTIVE_FAILURES = 1
        gate.check("qualsiasi cosa")
        self.assertFalse(gate.is_locked_out(), "senza passphrase configurata check() ritorna sempre False prima di contare nulla")


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
