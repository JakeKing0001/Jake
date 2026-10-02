"""F5.7 a voce: marcare un ricordo come segreto e chiedere quali ricordi ci sono. Memoria reale su database
temporaneo, dati sintetici."""
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from core.memory_privacy import MemoryPrivacyDashboard
from core.response_formatter import format_skill_result
from skills.memory_controls import ListMemoriesSkill, SetMemorySensitivitySkill


class MemorySensitivityControlsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)
        self.memory.remember("indirizzo di casa", "via Roma 1")
        self.memory.remember("codice del cancello", "4412")
        self.memory.remember("backup serale", '{"steps": ["copia"]}', category="workflow")
        self.dashboard = MemoryPrivacyDashboard(self.memory)

    def test_a_sensitive_memory_is_stored_encrypted_and_recalled_in_clear(self):
        # Regressione: _decrypt_if_needed chiamava SecretsVault.is_protected, che non esiste (AttributeError).
        self.memory.remember("numero tessera", "123456", sensitivity="sensitive")
        raw = self.memory._connection.execute(
            "SELECT value FROM memories WHERE key = ?", ("numero tessera",)).fetchone()[0]
        self.assertNotEqual(raw, "123456")
        self.assertEqual(self.memory.recall(key="numero tessera")[0]["value"], "123456")

    def test_marking_a_memory_secret_keeps_it_out_of_answers_and_exports(self):
        self.assertEqual([e["key"] for e in self.memory.relevant_for("qual e' il codice del cancello?")],
                         ["codice del cancello"])

        result = SetMemorySensitivitySkill(self.dashboard).execute({"key": "Codice del cancello", "sensitivity": "secret"})

        self.assertTrue(result.success, result)
        self.assertIn("ora è segreto", format_skill_result("SET_MEMORY_SENSITIVITY", result))
        self.assertEqual(self.memory.relevant_for("qual e' il codice del cancello?"), [])
        self.assertNotIn("4412", str(self.dashboard.export_payload()))

    def test_listing_shows_only_names_filtered_by_sensitivity_or_kind(self):
        SetMemorySensitivitySkill(self.dashboard).execute({"key": "codice del cancello", "sensitivity": "secret"})
        skill = ListMemoriesSkill(self.dashboard)

        secret = skill.execute({"sensitivity": "secret"})
        procedures = skill.execute({"kind": "procedure"})
        everything = skill.execute({})

        self.assertEqual(secret.data["keys"], ["codice del cancello"])
        self.assertNotIn("4412", format_skill_result("LIST_MEMORIES", secret), "mai i valori")
        self.assertEqual(procedures.data["keys"], ["backup serale"])
        self.assertEqual(everything.data["count"], 3)
        self.assertEqual(skill.execute({"kind": "boh"}).error, "INVALID_PARAMETERS")
