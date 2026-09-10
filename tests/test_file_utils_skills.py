"""Test unitari per skills/file_utils.py: nessuna suite esisteva finora. File/cartelle veri su
disco temporaneo (compressione/estrazione/copia reali), non simulati - e' proprio
l'interazione con shutil/Path a essere la parte interessante da verificare."""
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from skills.file_utils import (
    CompressPathSkill, CountWordsInFileSkill, DuplicateFileSkill, ExtractArchiveSkill,
    GetFileInfoSkill, GetFolderSizeSkill, ReadFileTextSkill,
)


class _WithTempDir(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_file_utils_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)


class CompressPathTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = CompressPathSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_path_fails(self):
        result = CompressPathSkill().execute({"path": str(self.tmp_dir / "non_esiste")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_compresses_a_real_folder(self):
        folder = self.tmp_dir / "cartella"
        folder.mkdir()
        (folder / "file.txt").write_text("ciao", encoding="utf-8")

        result = CompressPathSkill().execute({"path": str(folder)})

        self.assertTrue(result.success)
        archive = Path(result.data["archive_path"])
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as zf:
            self.assertIn("file.txt", zf.namelist())

    def test_compresses_a_single_file(self):
        file_path = self.tmp_dir / "documento.txt"
        file_path.write_text("contenuto", encoding="utf-8")

        result = CompressPathSkill().execute({"path": str(file_path)})

        self.assertTrue(result.success)
        with zipfile.ZipFile(result.data["archive_path"]) as zf:
            self.assertIn("documento.txt", zf.namelist())


class ExtractArchiveTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = ExtractArchiveSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_archive_fails(self):
        result = ExtractArchiveSkill().execute({"path": str(self.tmp_dir / "non_esiste.zip")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_extracts_a_real_archive(self):
        archive_path = self.tmp_dir / "archivio.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("dentro.txt", "contenuto")

        result = ExtractArchiveSkill().execute({"path": str(archive_path)})

        self.assertTrue(result.success)
        extracted = Path(result.data["destination"]) / "dentro.txt"
        self.assertEqual(extracted.read_text(encoding="utf-8"), "contenuto")

    def test_a_non_archive_file_fails_gracefully(self):
        fake_archive = self.tmp_dir / "non_un_vero.zip"
        fake_archive.write_text("questo non e' uno zip", encoding="utf-8")
        result = ExtractArchiveSkill().execute({"path": str(fake_archive)})
        self.assertEqual(result.error, "OPERATION_FAILED")


class GetFileInfoTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = GetFileInfoSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_path_fails(self):
        result = GetFileInfoSkill().execute({"path": str(self.tmp_dir / "non_esiste")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_reports_real_file_size_and_modification_date(self):
        file_path = self.tmp_dir / "documento.txt"
        file_path.write_bytes(b"0" * 2048)
        result = GetFileInfoSkill().execute({"path": str(file_path)})
        self.assertTrue(result.success)
        self.assertEqual(result.data["size_kb"], 2.0)
        self.assertFalse(result.data["is_folder"])

    def test_reports_a_folder_correctly(self):
        result = GetFileInfoSkill().execute({"path": str(self.tmp_dir)})
        self.assertTrue(result.success)
        self.assertTrue(result.data["is_folder"])


class CountWordsInFileTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = CountWordsInFileSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_file_fails(self):
        result = CountWordsInFileSkill().execute({"path": str(self.tmp_dir / "non_esiste.txt")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_counts_real_words(self):
        file_path = self.tmp_dir / "testo.txt"
        file_path.write_text("uno due tre quattro", encoding="utf-8")
        result = CountWordsInFileSkill().execute({"path": str(file_path)})
        self.assertTrue(result.success)
        self.assertEqual(result.data["words"], 4)


class ReadFileTextTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = ReadFileTextSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_reads_real_file_content(self):
        file_path = self.tmp_dir / "testo.txt"
        file_path.write_text("ciao mondo", encoding="utf-8")
        result = ReadFileTextSkill().execute({"path": str(file_path)})
        self.assertTrue(result.success)
        self.assertEqual(result.data["text"], "ciao mondo")
        self.assertFalse(result.data["truncated"])

    def test_long_content_is_truncated(self):
        file_path = self.tmp_dir / "lungo.txt"
        file_path.write_text("x" * (ReadFileTextSkill.MAX_CHARS + 500), encoding="utf-8")
        result = ReadFileTextSkill().execute({"path": str(file_path)})
        self.assertTrue(result.data["truncated"])
        self.assertEqual(len(result.data["text"]), ReadFileTextSkill.MAX_CHARS)


class DuplicateFileTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = DuplicateFileSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_file_fails(self):
        result = DuplicateFileSkill().execute({"path": str(self.tmp_dir / "non_esiste.txt")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_duplicates_a_real_file_with_a_copia_suffix(self):
        file_path = self.tmp_dir / "documento.txt"
        file_path.write_text("contenuto originale", encoding="utf-8")

        result = DuplicateFileSkill().execute({"path": str(file_path)})

        self.assertTrue(result.success)
        destination = Path(result.data["destination"])
        self.assertEqual(destination.name, "documento - copia.txt")
        self.assertEqual(destination.read_text(encoding="utf-8"), "contenuto originale")

    def test_duplicating_twice_increments_the_counter_instead_of_overwriting(self):
        file_path = self.tmp_dir / "documento.txt"
        file_path.write_text("originale", encoding="utf-8")
        DuplicateFileSkill().execute({"path": str(file_path)})

        result = DuplicateFileSkill().execute({"path": str(file_path)})

        self.assertEqual(Path(result.data["destination"]).name, "documento - copia 2.txt")


class GetFolderSizeTests(_WithTempDir):
    def test_missing_path_fails(self):
        result = GetFolderSizeSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_file_path_instead_of_a_folder_fails(self):
        file_path = self.tmp_dir / "documento.txt"
        file_path.write_text("x", encoding="utf-8")
        result = GetFolderSizeSkill().execute({"path": str(file_path)})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_sums_the_real_size_of_nested_files(self):
        (self.tmp_dir / "a.bin").write_bytes(b"0" * (1024 ** 2))
        subfolder = self.tmp_dir / "sub"
        subfolder.mkdir()
        (subfolder / "b.bin").write_bytes(b"0" * (1024 ** 2))

        result = GetFolderSizeSkill().execute({"path": str(self.tmp_dir)})

        self.assertTrue(result.success)
        self.assertEqual(result.data["size_mb"], 2.0)


if __name__ == "__main__":
    unittest.main()
