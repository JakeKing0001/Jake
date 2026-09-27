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



class ImportantConflictConfirmationTests(unittest.TestCase):
    """F5.4.3 sul JakeCore vero: cambiare un ricordo importante passa dalla conferma normale ("si'"/"no")."""

    def setUp(self):
        from core.command import Command
        from tests.test_jake_core_pipeline import FakeRegistry, FakeRouter, _bare_core

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)
        self.core = _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl",
                               skill_registry=FakeRegistry({"REMEMBER": RememberSkill(self.memory)}))
        self.Command, self.FakeRouter = Command, FakeRouter

    def say(self, text, parameters=None):
        if parameters is not None:
            self.core.router = self.FakeRouter(self.Command("REMEMBER", parameters))
        return self.core.answer(text)

    def test_an_important_memory_is_replaced_only_after_yes_and_stays_important(self):
        self.memory.remember("indirizzo di casa", "via Roma 1", importance=5)
        question = self.say("il mio indirizzo e' via Milano 2", {"key": "indirizzo di casa", "value": "via Milano 2"})
        self.assertIn('"via Roma 1"', question)
        self.assertEqual(self.memory.entry("indirizzo di casa")["value"], "via Roma 1", "niente prima del si'")
        self.assertIn("prima era via Roma 1", self.say("si"))
        current = self.memory.entry("indirizzo di casa")
        self.assertEqual((current["value"], current["importance"]), ("via Milano 2", 5))
        self.assertEqual(self.memory.versions("indirizzo di casa")[0]["value"], "via Roma 1")

    def test_no_keeps_the_important_memory_and_ordinary_ones_update_directly(self):
        self.memory.remember("gruppo sanguigno", "A+", importance=4)
        self.say("il mio gruppo e' B+", {"key": "gruppo sanguigno", "value": "B+"})
        self.say("no")
        self.assertEqual(self.memory.entry("gruppo sanguigno")["value"], "A+")

        self.memory.remember("snack preferito", "grissini")
        self.assertIn("prima era grissini", self.say("ora preferisco i taralli", {"key": "snack preferito", "value": "taralli"}))


if __name__ == "__main__":
    unittest.main()
