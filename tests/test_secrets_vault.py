"""Test unitari per la protezione dei segreti con Windows DPAPI (F1, Trustworthy Agent Core 3.0
- vedi core/secrets_vault.py). Chiama davvero win32crypt (nessun mock): DPAPI e' legato
all'account Windows di chi esegue i test, non serve un servizio esterno ne' una rete, quindi non
c'e' motivo di simularlo - a differenza di Ollama/NEST, evitati altrove nei test."""
import unittest

from core.secrets_vault import is_protected, protect, unprotect


class ProtectUnprotectRoundTripTests(unittest.TestCase):
    def test_round_trip_recovers_the_original_text(self):
        encrypted = protect("una-passphrase-segreta")
        self.assertEqual(unprotect(encrypted), "una-passphrase-segreta")

    def test_encrypted_value_does_not_contain_the_plaintext(self):
        encrypted = protect("token-home-assistant-abc123")
        self.assertNotIn("token-home-assistant-abc123", encrypted)

    def test_two_encryptions_of_the_same_text_still_both_decrypt_correctly(self):
        """DPAPI include dati casuali nella cifratura (non e' deterministica): due chiamate sullo
        stesso testo possono produrre blob diversi, ma devono decifrare entrambe allo stesso
        risultato - non e' un bug se i blob non sono identici."""
        first = protect("stesso-segreto")
        second = protect("stesso-segreto")
        self.assertEqual(unprotect(first), "stesso-segreto")
        self.assertEqual(unprotect(second), "stesso-segreto")

    def test_supports_unicode(self):
        encrypted = protect("città più caffè")
        self.assertEqual(unprotect(encrypted), "città più caffè")


class IsProtectedTests(unittest.TestCase):
    def test_encrypted_value_is_protected(self):
        self.assertTrue(is_protected(protect("x")))

    def test_plaintext_is_not_protected(self):
        self.assertFalse(is_protected("apri sesamo"))

    def test_empty_and_none_are_not_protected(self):
        self.assertFalse(is_protected(""))
        self.assertFalse(is_protected(None))


class UnprotectPassthroughTests(unittest.TestCase):
    def test_plaintext_passes_through_unchanged_instead_of_raising(self):
        """Un valore mai cifrato (non ancora migrato, o arrivato da una variabile d'ambiente
        JAKE_<CHIAVE>, che resta volutamente in chiaro - vedi core/config.py) non deve far
        fallire unprotect()."""
        self.assertEqual(unprotect("apri sesamo"), "apri sesamo")

    def test_empty_string_passes_through(self):
        self.assertEqual(unprotect(""), "")


if __name__ == "__main__":
    unittest.main()
