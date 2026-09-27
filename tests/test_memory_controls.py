"""F5.7 a voce: "esporta i miei ricordi" e "fissa il ricordo X". Memoria reale su database temporaneo, dati
sintetici, export in una cartella temporanea (mai Documenti reali nei test)."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core.memory_manager import MemoryManager
from core.memory_privacy import MemoryPrivacyDashboard
from core.response_formatter import format_skill_result
from skills.memory_controls import ExportMemoriesSkill, PinMemorySkill
from skills.remember import RememberSkill


class MemoryControlsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.memory = MemoryManager(self.tmp / "memory.db")
        self.addCleanup(self.memory.close)
        self.memory.remember("indirizzo di casa", "via Roma 1")
        self.memory.remember("pin del bancomat", "12345", sensitivity="secret")
        self.dashboard = MemoryPrivacyDashboard(self.memory)

    def test_export_writes_markdown_and_json_without_secrets(self):
        skill = ExportMemoriesSkill(self.dashboard, export_dir=self.tmp / "export", clock=lambda: datetime(2026, 9, 27, 18, 0))
        result = skill.execute({})
        self.assertTrue(result.success, result)
        markdown = Path(result.data["markdown"]).read_text(encoding="utf-8")
        self.assertIn("via Roma 1", markdown)
        self.assertNotIn("12345", markdown + Path(result.data["json"]).read_text(encoding="utf-8"))
        self.assertEqual(json.loads(Path(result.data["json"]).read_text(encoding="utf-8"))["format"], "jake-memory-export")
        reply = format_skill_result("EXPORT_MEMORIES", result)
        self.assertIn("Ho esportato 1 ricordi", reply)
        self.assertIn("1 ricordi segreti non sono inclusi", reply)

    def test_pinning_makes_a_later_change_ask_for_confirmation(self):
        result = PinMemorySkill(self.dashboard).execute({"key": "indirizzo di casa"})
        self.assertIn("è fissato", format_skill_result("PIN_MEMORY", result))
        self.assertEqual(RememberSkill(self.memory).execute({"key": "indirizzo di casa", "value": "via Milano 2"}).error,
                         "CONFIRMATION_REQUIRED")
        PinMemorySkill(self.dashboard).execute({"key": "indirizzo di casa", "pinned": False})
        self.assertTrue(RememberSkill(self.memory).execute({"key": "indirizzo di casa", "value": "via Milano 2"}).success)
        self.assertEqual(PinMemorySkill(self.dashboard).execute({"key": "numero di scarpe"}).error, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
