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
        prefix, b64_payload = encrypted.rsplit(":", 1)  # "dpapi:1" / base64 - vedi VersionedFormatTests
        raw = base64.b64decode(b64_payload)
        corrupted = bytes((b ^ 0xFF) for b in raw)  # capovolge ogni bit: un blob DPAPI vero ma illeggibile
        tampered_value = prefix + ":" + base64.b64encode(corrupted).decode("ascii")

        self.assertIsNone(unprotect(tampered_value))


class VersionedFormatTests(unittest.TestCase):
    """F1.4.1 ("consolidare DPAPI in un SecretsVault con versione e migrazione atomica"): prima
    di questa correzione il blob non portava alcun tag di versione ("dpapi:<base64>"), quindi un
    formato futuro diverso non avrebbe avuto modo di distinguersi da quello attuale ne' di
    coesistere con blob vecchi gia' su disco."""

    def test_protect_produces_a_versioned_blob(self):
        from core.secrets_vault import CURRENT_VERSION

        encrypted = protect("x")

        self.assertTrue(encrypted.startswith(f"dpapi:{CURRENT_VERSION}:"))

    def test_a_legacy_blob_without_a_version_tag_still_decrypts(self):
        """Un blob "dpapi:<base64>" senza segmento di versione - il formato prodotto da
        installazioni precedenti a questa correzione - deve restare decifrabile per sempre, non
        diventare illeggibile solo perche' il codice e' cambiato."""
        import win32crypt

        encrypted = win32crypt.CryptProtectData("segreto-legacy".encode("utf-8"), None, None, None, None, 0)
        import base64
        legacy_value = "dpapi:" + base64.b64encode(encrypted).decode("ascii")

        self.assertEqual(unprotect(legacy_value), "segreto-legacy")

    def test_an_unsupported_future_version_returns_none_instead_of_raising(self):
        """Un blob scritto da una versione FUTURA di Jake, ancora sconosciuta a questo codice:
        nega per default invece di tentare comunque la decifratura su un formato che potrebbe non
        essere nemmeno DPAPI."""
        encrypted = protect("x")
        _prefix, _version, b64_payload = encrypted.split(":", 2)
        from_the_future = f"dpapi:99:{b64_payload}"

        self.assertIsNone(unprotect(from_the_future))


class NeedsMigrationTests(unittest.TestCase):
    """SecretsVault.needs_migration() guida Config._migrate_secrets() nel ricifrare sul posto un
    segreto gia' protetto ma nel formato legacy, non solo uno ancora in chiaro."""

    def test_a_blob_produced_by_the_current_vault_does_not_need_migration(self):
        from core.secrets_vault import SecretsVault

        vault = SecretsVault()

        self.assertFalse(vault.needs_migration(vault.protect("x")))

    def test_a_legacy_unversioned_blob_needs_migration(self):
        import base64

        import win32crypt

        from core.secrets_vault import SecretsVault

        vault = SecretsVault()
        encrypted = win32crypt.CryptProtectData("x".encode("utf-8"), None, None, None, None, 0)
        legacy_value = "dpapi:" + base64.b64encode(encrypted).decode("ascii")

        self.assertTrue(vault.needs_migration(legacy_value))

    def test_plaintext_does_not_need_migration(self):
        """Un valore mai protetto non e' compito di needs_migration() - quello lo copre gia' il
        ramo "not is_protected" di Config._migrate_secrets(), protect() semplice."""
        from core.secrets_vault import SecretsVault

        self.assertFalse(SecretsVault().needs_migration("testo in chiaro"))


if __name__ == "__main__":
    unittest.main()
