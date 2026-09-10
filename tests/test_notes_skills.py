"""Test unitari per skills/notes.py: nessuna suite esisteva finora. NOTES_PATH e' una costante
di MODULO puntata alla vera data/notes.md (nessuna iniezione via costruttore): ogni test qui
sotto patcha skills.notes.NOTES_PATH su un file temporaneo, altrimenti scriverebbe/cancellerebbe
per davvero gli appunti reali dell'utente sul disco - da tenere a mente per ogni test futuro
aggiunto a questo file.

F1 (indiretto): buco reale trovato e corretto in questa sessione in LIST_NOTES. limit=0
restituiva TUTTI gli appunti invece di zero (lines[-0:] in Python e' l'intera lista, la stessa
insidia di '-0 == 0'), e un limit non numerico (es. il modello che scrive 'tutti' invece di un
numero) sollevava un ValueError mai catturato."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import skills.notes as notes_module
from skills.notes import AddNoteSkill, ClearNotesSkill, ExportNotesSkill, ListNotesSkill, SearchNotesSkill


class _WithTempNotesFile(unittest.TestCase):
    def setUp(self):
        tmp_path = Path(tempfile.mkdtemp(prefix="jake_notes_test_")) / "notes.md"
        self._patcher = mock.patch.object(notes_module, "NOTES_PATH", tmp_path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.notes_path = tmp_path

    def _seed(self, *lines: str) -> None:
        self.notes_path.parent.mkdir(parents=True, exist_ok=True)
        self.notes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class AddNoteTests(_WithTempNotesFile):
    def test_missing_text_fails(self):
        result = AddNoteSkill().execute({"text": ""})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_note_is_actually_appended_to_disk_with_a_timestamp(self):
        result = AddNoteSkill().execute({"text": "comprare il latte"})
        self.assertTrue(result.success)
        content = self.notes_path.read_text(encoding="utf-8")
        self.assertIn("comprare il latte", content)
        self.assertRegex(content, r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]")

    def test_multiple_notes_accumulate_without_overwriting(self):
        AddNoteSkill().execute({"text": "primo"})
        AddNoteSkill().execute({"text": "secondo"})
        content = self.notes_path.read_text(encoding="utf-8")
        self.assertIn("primo", content)
        self.assertIn("secondo", content)


class ListNotesTests(_WithTempNotesFile):
    def test_no_notes_file_reports_not_found(self):
        result = ListNotesSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_the_default_number_of_recent_notes(self):
        self._seed(*[f"- appunto {i}" for i in range(15)])
        result = ListNotesSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["notes"]), 10)
        self.assertEqual(result.data["notes"][-1], "- appunto 14")

    def test_respects_an_explicit_limit(self):
        self._seed(*[f"- appunto {i}" for i in range(15)])
        result = ListNotesSkill().execute({"limit": 3})
        self.assertEqual(len(result.data["notes"]), 3)


class ListNotesLimitEdgeCaseRegressionTests(_WithTempNotesFile):
    """Il buco reale trovato e corretto in questa sessione."""

    def test_limit_zero_falls_back_to_the_default_instead_of_returning_everything(self):
        self._seed(*[f"- appunto {i}" for i in range(15)])
        result = ListNotesSkill().execute({"limit": 0})
        self.assertEqual(len(result.data["notes"]), ListNotesSkill.DEFAULT_LIMIT)

    def test_negative_limit_falls_back_to_the_default(self):
        self._seed(*[f"- appunto {i}" for i in range(15)])
        result = ListNotesSkill().execute({"limit": -5})
        self.assertEqual(len(result.data["notes"]), ListNotesSkill.DEFAULT_LIMIT)

    def test_non_numeric_limit_falls_back_to_the_default_instead_of_crashing(self):
        self._seed(*[f"- appunto {i}" for i in range(15)])
        result = ListNotesSkill().execute({"limit": "tutti"})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["notes"]), ListNotesSkill.DEFAULT_LIMIT)


class SearchNotesTests(_WithTempNotesFile):
    def test_missing_query_fails(self):
        result = SearchNotesSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_no_notes_file_reports_not_found(self):
        result = SearchNotesSkill().execute({"query": "latte"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_finds_a_matching_note_case_insensitively(self):
        self._seed("- comprare il LATTE", "- portare fuori il cane")
        result = SearchNotesSkill().execute({"query": "latte"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["notes"], ["- comprare il LATTE"])

    def test_no_match_reports_not_found(self):
        self._seed("- comprare il latte")
        result = SearchNotesSkill().execute({"query": "qualcosa che non c'e'"})
        self.assertEqual(result.error, "NOT_FOUND")


class ExportNotesTests(_WithTempNotesFile):
    def test_missing_path_fails(self):
        result = ExportNotesSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_no_notes_file_reports_not_found(self):
        result = ExportNotesSkill().execute({"path": str(self.notes_path.parent / "copia.md")})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_exports_a_real_copy_of_the_notes(self):
        self._seed("- comprare il latte")
        destination = self.notes_path.parent / "copia.md"
        result = ExportNotesSkill().execute({"path": str(destination)})
        self.assertTrue(result.success)
        self.assertEqual(destination.read_text(encoding="utf-8"), self.notes_path.read_text(encoding="utf-8"))


class ClearNotesTests(_WithTempNotesFile):
    def test_no_notes_file_reports_not_found(self):
        result = ClearNotesSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_without_confirmation_asks_for_it_and_does_not_delete_the_file(self):
        self._seed("- comprare il latte")
        result = ClearNotesSkill().execute({})
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertTrue(self.notes_path.is_file())

    def test_confirmed_clearing_actually_deletes_the_file(self):
        self._seed("- comprare il latte")
        result = ClearNotesSkill().execute({"confirmed": True})
        self.assertTrue(result.success)
        self.assertFalse(self.notes_path.exists())


if __name__ == "__main__":
    unittest.main()
