"""Test unitari per skills/file_utils2.py: nessuna suite esisteva finora. File veri su disco
temporaneo, dimensioni/contenuti reali - e' proprio os.stat/hashlib a essere la parte
interessante da verificare, non simulabile senza perdere valore."""
import shutil
import tempfile
import unittest
from pathlib import Path

from skills.file_utils2 import FindDuplicateFilesSkill, FindLargeFilesSkill


class _WithTempDir(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_file_utils2_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)


class FindLargeFilesTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = FindLargeFilesSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_path_fails(self):
        result = FindLargeFilesSkill().execute({"path": str(self.tmp_dir / "non_esiste")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_no_files_above_the_threshold_reports_not_found(self):
        (self.tmp_dir / "piccolo.bin").write_bytes(b"0" * 1024)
        result = FindLargeFilesSkill().execute({"path": str(self.tmp_dir), "min_size_mb": 1})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_finds_files_above_the_threshold_sorted_by_size_descending(self):
        (self.tmp_dir / "medio.bin").write_bytes(b"0" * (2 * 1024 ** 2))
        (self.tmp_dir / "grande.bin").write_bytes(b"0" * (5 * 1024 ** 2))
        (self.tmp_dir / "piccolo.bin").write_bytes(b"0" * 1024)

        result = FindLargeFilesSkill().execute({"path": str(self.tmp_dir), "min_size_mb": 1})

        self.assertTrue(result.success)
        names = [Path(f["path"]).name for f in result.data["files"]]
        self.assertEqual(names, ["grande.bin", "medio.bin"])

    def test_non_numeric_min_size_falls_back_to_the_default(self):
        (self.tmp_dir / "piccolo.bin").write_bytes(b"0" * 1024)
        result = FindLargeFilesSkill().execute({"path": str(self.tmp_dir), "min_size_mb": "grande"})
        # con il default (100 MB) nessun file di questo test lo supera
        self.assertEqual(result.error, "NOT_FOUND")

    def test_subfolders_are_scanned_too(self):
        subfolder = self.tmp_dir / "sub"
        subfolder.mkdir()
        (subfolder / "grande.bin").write_bytes(b"0" * (2 * 1024 ** 2))
        result = FindLargeFilesSkill().execute({"path": str(self.tmp_dir), "min_size_mb": 1})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["files"]), 1)


class FindDuplicateFilesTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = FindDuplicateFilesSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_path_fails(self):
        result = FindDuplicateFilesSkill().execute({"path": str(self.tmp_dir / "non_esiste")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_no_duplicates_reports_not_found(self):
        (self.tmp_dir / "a.txt").write_text("contenuto a", encoding="utf-8")
        (self.tmp_dir / "b.txt").write_text("contenuto b", encoding="utf-8")
        result = FindDuplicateFilesSkill().execute({"path": str(self.tmp_dir)})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_identical_content_under_different_names_is_detected_as_duplicate(self):
        (self.tmp_dir / "originale.txt").write_text("stesso contenuto", encoding="utf-8")
        (self.tmp_dir / "rinominato.txt").write_text("stesso contenuto", encoding="utf-8")

        result = FindDuplicateFilesSkill().execute({"path": str(self.tmp_dir)})

        self.assertTrue(result.success)
        self.assertEqual(len(result.data["duplicate_groups"]), 1)
        names = {Path(p).name for p in result.data["duplicate_groups"][0]}
        self.assertEqual(names, {"originale.txt", "rinominato.txt"})

    def test_same_size_different_content_is_not_a_false_positive(self):
        """Confronta per hash del contenuto, non solo dimensione: due file della stessa
        lunghezza ma con byte diversi non devono risultare duplicati."""
        (self.tmp_dir / "a.txt").write_text("aaaaaaaaaa", encoding="utf-8")
        (self.tmp_dir / "b.txt").write_text("bbbbbbbbbb", encoding="utf-8")
        result = FindDuplicateFilesSkill().execute({"path": str(self.tmp_dir)})
        self.assertEqual(result.error, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
