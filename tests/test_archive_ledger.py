"""Test unitari per l'archivio in sola lettura del ledger (F1.7.3, vedi tools/archive_ledger.py).
Nessun test qui deve toccare il ledger vero del progetto: ogni test usa una cartella temporanea."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.archive_ledger import _load_jsonl, archive, default_archive_path, split_by_age


class LoadJsonlTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "ledger.jsonl"

    def test_missing_file_returns_empty_list(self):
        self.assertEqual(_load_jsonl(self.path), [])

    def test_malformed_lines_are_skipped_not_fatal(self):
        self.path.write_text('{"ts": 1}\nnon e json valido\n{"ts": 2}\n', encoding="utf-8")

        self.assertEqual(_load_jsonl(self.path), [{"ts": 1}, {"ts": 2}])


class SplitByAgeTests(unittest.TestCase):
    NOW = 1_000_000.0
    DAY = 86400

    def test_entries_older_than_the_cutoff_are_archived(self):
        records = [{"ts": self.NOW - 200 * self.DAY}, {"ts": self.NOW - 1 * self.DAY}]

        old, recent = split_by_age(records, older_than_days=180, now=self.NOW)

        self.assertEqual(old, [records[0]])
        self.assertEqual(recent, [records[1]])

    def test_entry_exactly_at_the_cutoff_is_not_archived(self):
        """Nega per default: un confine ambiguo resta tra le RECENTI, mai archiviato per errore."""
        records = [{"ts": self.NOW - 180 * self.DAY}]

        old, recent = split_by_age(records, older_than_days=180, now=self.NOW)

        self.assertEqual(old, [])
        self.assertEqual(recent, records)

    def test_a_record_without_a_valid_ts_is_never_archived(self):
        """Un record che non si riesce a datare (ts assente, o non numerico) resta prudentemente
        tra le recenti - lo stesso principio "nega per default" gia' applicato altrove in F1."""
        records = [{"ts": "non-un-numero"}, {"intent": "OPEN_APP"}]

        old, recent = split_by_age(records, older_than_days=1, now=self.NOW)

        self.assertEqual(old, [])
        self.assertEqual(recent, records)

    def test_empty_input_does_not_crash(self):
        self.assertEqual(split_by_age([], older_than_days=180, now=self.NOW), ([], []))


class DefaultArchivePathTests(unittest.TestCase):
    def test_path_is_derived_from_the_source_and_the_cutoff_date(self):
        source = Path("data/jake_ledger.jsonl")

        result = default_archive_path(source, older_than_days=0, now=1_700_000_000.0)

        self.assertEqual(result.parent, source.parent)
        self.assertTrue(result.name.startswith("jake_ledger_archive_before_"))
        self.assertTrue(result.name.endswith(".jsonl"))


class ArchiveEndToEndTests(unittest.TestCase):
    """F1.7.3: la garanzia che conta e' che il ledger ORIGINALE non venga mai toccato - verificato
    con file temporanei reali, non solo con le funzioni pure sopra."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_dir = Path(self._tmpdir.name)
        self.ledger_path = self.tmp_dir / "jake_ledger.jsonl"

    def _write_ledger(self, records):
        self.ledger_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8",
        )

    def test_old_entries_are_copied_to_the_archive_and_the_source_is_untouched(self):
        import time
        now = time.time()
        old_record = {"ts": now - 200 * 86400, "intent": "DELETE_PATH", "result": "success"}
        recent_record = {"ts": now - 1 * 86400, "intent": "OPEN_APP", "result": "success"}
        self._write_ledger([old_record, recent_record])
        original_content = self.ledger_path.read_text(encoding="utf-8")
        output_path = self.tmp_dir / "archive.jsonl"

        result = archive(self.ledger_path, older_than_days=180, output=output_path)

        self.assertEqual(result, {"total": 2, "archived": 1, "remaining": 1, "output": output_path})
        self.assertEqual(
            self.ledger_path.read_text(encoding="utf-8"), original_content,
            "il ledger originale non deve mai essere modificato",
        )
        archived = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(archived, [old_record])

    def test_no_output_file_is_written_when_nothing_is_old_enough(self):
        import time
        now = time.time()
        self._write_ledger([{"ts": now - 1 * 86400, "intent": "OPEN_APP", "result": "success"}])
        output_path = self.tmp_dir / "archive.jsonl"

        result = archive(self.ledger_path, older_than_days=180, output=output_path)

        self.assertEqual(result["archived"], 0)
        self.assertIsNone(result["output"])
        self.assertFalse(output_path.exists())

    def test_missing_source_ledger_does_not_crash(self):
        result = archive(self.tmp_dir / "non_esiste.jsonl", older_than_days=180)
        self.assertEqual(result, {"total": 0, "archived": 0, "remaining": 0, "output": None})


if __name__ == "__main__":
    unittest.main()
