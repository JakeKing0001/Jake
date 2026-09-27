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
