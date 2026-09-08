"""Test unitari per Config (core/config.py), in particolare la protezione DPAPI dei segreti (F1
- vedi core/secrets_vault.py, tests/test_secrets_vault.py per la cifratura in isolamento). Ogni
test lavora su un file temporaneo, mai su config/settings.json vero."""
import json
import tempfile
import unittest
from pathlib import Path

from core.config import Config
from core.secrets_vault import is_protected, unprotect


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "settings.json"

    def _write_raw(self, values: dict) -> None:
        self.path.write_text(json.dumps(values), encoding="utf-8")


class NonSecretValuesTests(ConfigTestCase):
    def test_ordinary_values_round_trip_unchanged(self):
        config = Config(path=self.path)
        config.set("ollama_model", "qwen2.5:7b")
        self.assertEqual(config.get("ollama_model"), "qwen2.5:7b")

    def test_falsy_stored_value_is_respected_not_replaced_by_default(self):
        config = Config(path=self.path)
        config.set("system_advisor_enabled", False)
        self.assertFalse(config.get("system_advisor_enabled", True))


class SecretEncryptionAtRestTests(ConfigTestCase):
    def test_set_stores_an_encrypted_value_on_disk(self):
        config = Config(path=self.path)
        config.set("admin_passphrase", "apri sesamo")

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(is_protected(raw["admin_passphrase"]))
        self.assertNotIn("apri sesamo", self.path.read_text(encoding="utf-8"))

    def test_get_transparently_decrypts_what_set_encrypted(self):
        config = Config(path=self.path)
        config.set("home_assistant_token", "token-vero-abc123")

        self.assertEqual(config.get("home_assistant_token"), "token-vero-abc123")

    def test_a_fresh_config_instance_can_still_decrypt_a_previously_saved_secret(self):
        """Il segreto deve sopravvivere a un riavvio di Jake (nuova istanza di Config), non solo
        restare leggibile finche' lo stesso oggetto Python e' vivo."""
        Config(path=self.path).set("admin_passphrase", "apri sesamo")

        reloaded = Config(path=self.path)

        self.assertEqual(reloaded.get("admin_passphrase"), "apri sesamo")


class MigrationOfPlaintextSecretsTests(ConfigTestCase):
    def test_plaintext_secret_from_an_older_version_is_encrypted_on_load(self):
        """F1: 'migrazione sicura dei token gia' salvati' - un file scritto da una versione di
        Jake precedente a questo modulo, con la passphrase ancora in chiaro, deve essere cifrato
        automaticamente alla prima apertura, senza che l'utente debba fare nulla."""
        self._write_raw({"admin_passphrase": "vecchia-passphrase-in-chiaro"})

        Config(path=self.path)  # basta costruirlo: la migrazione avviene in __init__

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(is_protected(raw["admin_passphrase"]))
        self.assertEqual(unprotect(raw["admin_passphrase"]), "vecchia-passphrase-in-chiaro")

    def test_migrated_secret_is_still_readable_through_get(self):
        self._write_raw({"home_assistant_token": "vecchio-token-in-chiaro"})

        config = Config(path=self.path)

        self.assertEqual(config.get("home_assistant_token"), "vecchio-token-in-chiaro")

    def test_already_encrypted_secret_is_not_touched_again(self):
        """Nessuna doppia cifratura a ogni avvio: se e' gia' protetto, _migrate_secrets non deve
        ne' riscriverlo ne' toccare il file."""
        Config(path=self.path).set("admin_passphrase", "apri sesamo")
        before = self.path.read_text(encoding="utf-8")

        Config(path=self.path)  # una seconda apertura

        after = self.path.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_non_secret_keys_are_never_encrypted(self):
        self._write_raw({"ollama_model": "qwen2.5:7b", "admin_passphrase": "x"})

        Config(path=self.path)

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["ollama_model"], "qwen2.5:7b")


class EnvironmentVariableOverrideTests(ConfigTestCase):
    def test_env_var_overrides_stored_secret_and_stays_plaintext(self):
        """Le variabili d'ambiente JAKE_<CHIAVE> restano intenzionalmente in chiaro (vedi il
        docstring di Config): sono gia' il modo per non scrivere affatto il segreto su disco,
        DPAPI non li tocca."""
        import os

        Config(path=self.path).set("admin_passphrase", "quella-nel-file")
        os.environ["JAKE_ADMIN_PASSPHRASE"] = "quella-nella-env"
        self.addCleanup(lambda: os.environ.pop("JAKE_ADMIN_PASSPHRASE", None))

        config = Config(path=self.path)

        self.assertEqual(config.get("admin_passphrase"), "quella-nella-env")


if __name__ == "__main__":
    unittest.main()
