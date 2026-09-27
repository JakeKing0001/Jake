"""F5.4.1/F5.4.2: un ricordo non si sovrascrive piu' in silenzio. Database temporaneo, dati sintetici."""
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from core.memory_privacy import MemoryPrivacyDashboard
from core.response_formatter import format_skill_result
from skills.remember import RememberSkill


class MemoryConflictTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)

    def test_a_user_update_keeps_the_previous_version_and_says_so(self):
        skill = RememberSkill(self.memory)
        skill.execute({"key": "citta'", "value": "Roma"})
        result = skill.execute({"key": "citta'", "value": "Milano"})
        self.assertEqual(format_skill_result("REMEMBER", result), "Ok, ho aggiornato citta': ora è Milano (prima era Roma).")
        self.assertEqual(self.memory.recall(key="citta'")[0]["value"], "Milano")
        self.assertEqual([(v["value"], v["reason"]) for v in self.memory.versions("citta'")], [("Roma", "superseded")])

    def test_an_inference_never_overwrites_what_the_user_said(self):
        self.memory.remember("colore preferito", "verde", source="user")
        outcome = self.memory.remember("colore preferito", "blu", source="inferred")
        self.assertEqual(outcome, {"status": "conflict", "previous": "verde"})
        self.assertEqual(self.memory.recall(key="colore preferito")[0]["value"], "verde")
        self.assertEqual(self.memory.versions("colore preferito")[0]["reason"], "conflict_rejected")

    def test_same_value_is_unchanged_and_structured_records_still_replace_themselves(self):
        self.assertEqual(self.memory.remember("lingua", "italiano")["status"], "created")
        self.assertEqual(self.memory.remember("lingua", " Italiano ")["status"], "unchanged")
        self.memory.remember("backup serale", '{"steps": 1}', category="procedure")
        self.assertEqual(self.memory.remember("backup serale", '{"steps": 2}', category="procedure")["status"], "updated")
        self.assertEqual(self.memory.versions("backup serale", "procedure"), [])

    def test_forgetting_also_removes_the_previous_versions(self):
        self.memory.remember("indirizzo di prova", "via Vecchia 1", source="user")
        self.memory.remember("indirizzo di prova", "via Nuova 2", source="user")
        receipt = MemoryPrivacyDashboard(self.memory).delete("indirizzo di prova")
        self.assertEqual(receipt.rows_removed["memory_versions"], 1)
        self.assertTrue(receipt.verified)
        self.assertEqual(self.memory.versions("indirizzo di prova"), [])


if __name__ == "__main__":
    unittest.main()
