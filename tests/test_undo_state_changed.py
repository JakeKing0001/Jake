"""F4.6.4: "annulla l'ultima azione" non cancella cio' che Jake ha creato se nel frattempo l'utente ci ha lavorato
(file modificato, file aggiunti nella cartella estratta): lo rifiuta e spiega perche'. File e cartelle reali in una
cartella temporanea."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.response_formatter import format_skill_result
from core.undo_store import UndoStore, generate_undo_descriptor
from skills.undo import UndoLastActionSkill


class UndoStateChangedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = UndoStore()
        self.skill = UndoLastActionSkill(SimpleNamespace(undo_store=self.store))

    def _created(self, intent, key, path):
        self.store.save(generate_undo_descriptor(f"a-{intent}", intent, {key: str(path)}))

    def test_an_untouched_file_can_be_undone_a_modified_one_cannot(self):
        note = self.root / "appunti.txt"
        note.write_text("", encoding="utf-8")
        self._created("CREATE_PATH", "path", note)
        self.assertEqual(self.skill.execute().error, "CONFIRMATION_REQUIRED", "non toccato: si chiede solo conferma")

        note.write_text("due ore di lavoro", encoding="utf-8")
        os.utime(note, (time.time() + 5, time.time() + 5))  # mtime certo diverso anche su filesystem a bassa risoluzione
        result = self.skill.execute()
        self.assertEqual(result.error, "UNDO_STATE_CHANGED")
        self.assertIn("è cambiato dopo la mia azione", format_skill_result("UNDO_LAST_ACTION", result))
        self.assertTrue(note.exists())

    def test_files_added_to_an_extracted_folder_block_the_undo(self):
        folder = self.root / "estratto"
        folder.mkdir()
        (folder / "dentro.txt").write_text("dall'archivio", encoding="utf-8")
        self._created("EXTRACT_ARCHIVE", "destination", folder)
        (folder / "mio.docx").write_text("aggiunto dall'utente", encoding="utf-8")
        self.assertEqual(self.skill.execute().error, "UNDO_STATE_CHANGED")


if __name__ == "__main__":
    unittest.main()


class UndoConfirmationWindowTests(unittest.TestCase):
    """F4.6.4 al "si'": lo stato si ricontrolla al momento dell'esecuzione e un undo fatto non si ripete. JakeCore
    reale (policy, conferma), DeletePathSkill vera, file reali."""

    def setUp(self):
        from core.command import Command
        from skills.delete_path import DeletePathSkill
        from tests.test_jake_core_pipeline import FakeRegistry, FakeRouter, _bare_core

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.note = self.root / "creato-da-jake.txt"
        self.note.write_text("", encoding="utf-8")
        registry = FakeRegistry({"DELETE_PATH": DeletePathSkill()})
        self.core = _bare_core(ledger_path=self.root / "ledger.jsonl", skill_registry=registry,
                               router=FakeRouter(Command("UNDO_LAST_ACTION", {})))
        registry._skills["UNDO_LAST_ACTION"] = UndoLastActionSkill(self.core)
        self.core.undo_store.save(generate_undo_descriptor("crea-1", "CREATE_PATH", {"path": str(self.note)}))

    def test_a_change_between_question_and_yes_stops_the_undo(self):
        self.assertIn("Vuoi annullare", self.core.answer("annulla l'ultima azione"))
        self.note.write_text("scritto mentre Jake aspettava", encoding="utf-8")
        os.utime(self.note, (time.time() + 5, time.time() + 5))
        self.assertIn("è cambiato mentre aspettavo la conferma", self.core.answer("si"))
        self.assertTrue(self.note.exists())

    def test_an_undo_done_is_consumed_and_cannot_be_repeated(self):
        self.core.answer("annulla l'ultima azione")
        self.core.answer("si")
        self.assertFalse(self.note.exists())
        self.assertIn("Non c'è nessuna azione recente", self.core.answer("annulla l'ultima azione"))
