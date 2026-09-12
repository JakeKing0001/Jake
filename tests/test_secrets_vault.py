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


class CorruptedVaultTests(unittest.TestCase):
    """F1.4.8 ("testare... vault corrotto, profilo Windows differente e backup"): buco reale
    trovato e corretto - unprotect() sollevava un'eccezione non catturata (binascii.Error per un
    base64 malformato, pywintypes.error per un blob DPAPI incompatibile) che si propagava fino a
    Config.get(), chiamato da JakeCore.__init__ per costruire AuthGate: un SINGOLO segreto
    illeggibile faceva crashare l'avvio INTERO di Jake, non solo l'autenticazione. Riprodotto per
    davvero prima di correggere (un valore con prefisso "dpapi:" ma bytes non validi sollevava
    davvero), non solo ipotizzato leggendo il codice."""

    def test_malformed_base64_does_not_raise(self):
        """Il prefisso e' presente ma cio' che segue non e' nemmeno base64 valido - un file
        modificato a mano, o troncato da una scrittura interrotta a meta'."""
        self.assertIsNone(unprotect("dpapi:non-e-base64-valido!!!"))

    def test_valid_base64_but_not_a_real_dpapi_blob_does_not_raise(self):
        """Base64 sintatticamente valido, ma i bytes decodificati non sono un blob DPAPI vero:
        win32crypt.CryptUnprotectData deve rifiutarlo, non propagare l'eccezione."""
        import base64

        garbage = base64.b64encode(b"non sono affatto un blob DPAPI valido, solo bytes a caso").decode("ascii")
        self.assertIsNone(unprotect(f"dpapi:{garbage}"))

    def test_a_real_blob_with_flipped_bytes_does_not_raise(self):
        """Un blob DPAPI VERO (non uno finto), ma corrotto dopo la cifratura - simula un file
        danneggiato da una sincronizzazione interrotta o un backup parziale, non solo un valore
        mai stato valido."""
        import base64

        encrypted = protect("segreto-vero-prima-della-corruzione")
        raw = base64.b64decode(encrypted[len("dpapi:"):])
        corrupted = bytes((b ^ 0xFF) for b in raw)  # capovolge ogni bit: un blob DPAPI vero ma illeggibile
        tampered_value = "dpapi:" + base64.b64encode(corrupted).decode("ascii")

        self.assertIsNone(unprotect(tampered_value))


if __name__ == "__main__":
    unittest.main()
