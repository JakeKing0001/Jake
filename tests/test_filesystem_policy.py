"""Test unitari per core/filesystem_policy.py::is_protected_path (F1, Trustworthy Agent Core
3.0 - vedi ROADMAP.md). Usato da DELETE_PATH/RENAME_PATH/MOVE_PATH per rifiutare a priori
un'operazione su una cartella critica, PRIMA ancora della normale conferma si'/no - un buco qui
significa che una singola risposta affermativa (anche fraintesa da un comando vocale) potrebbe
cancellare qualcosa di catastrofico. Il modulo non aveva ancora nessun test."""
import os
import unittest
from pathlib import Path
from unittest import mock

from core.filesystem_policy import is_protected_path


class AnyDriveRootIsProtectedTests(unittest.TestCase):
    """F1: buco reale trovato e corretto - la docstring ha sempre promesso "radice del disco"
    in generale, ma il controllo verificava solo SystemDrive (tipicamente C:): la radice di un
    secondo disco (D:, un SSD esterno...) non era mai protetta. DELETE_PATH su D:\\ passava
    dalla normale conferma invece di essere rifiutato a priori come C:\\."""

    def test_system_drive_root_is_protected(self):
        system_drive = os.environ.get("SystemDrive", "C:")
        self.assertTrue(is_protected_path(Path(f"{system_drive}\\")))

    def test_a_non_system_drive_root_is_also_protected(self):
        self.assertTrue(is_protected_path(Path("D:\\")))
        self.assertTrue(is_protected_path(Path("Z:\\")))

    def test_a_subfolder_of_a_non_system_drive_is_not_protected_by_the_root_check(self):
        """Solo la RADICE e' protetta a priori: un file/cartella dentro D:\\ resta soggetto
        alla normale conferma, non a un rifiuto automatico - "non blocca il resto del disco"."""
        self.assertFalse(is_protected_path(Path("D:\\qualche_cartella")))


class HomeDirectoryTests(unittest.TestCase):
    def test_home_directory_itself_is_protected(self):
        self.assertTrue(is_protected_path(Path.home()))

    def test_a_subfolder_of_home_is_not_protected_by_the_home_check(self):
        """Stessa logica della radice del disco: solo la home ESATTA e' rifiutata a priori,
        Desktop/Documenti/Download restano soggetti alla normale conferma."""
        self.assertFalse(is_protected_path(Path.home() / "Desktop"))
        self.assertFalse(is_protected_path(Path.home() / "Documents"))


class SystemSubtreeTests(unittest.TestCase):
    """WINDIR/ProgramFiles/ProgramFiles(x86)/ProgramData: l'INTERO contenuto e' protetto, non
    solo la cartella stessa - vedi _subtree_blocked_roots()."""

    def test_windir_root_is_protected(self):
        with mock.patch.dict(os.environ, {"WINDIR": "C:\\FakeWindows"}, clear=False):
            self.assertTrue(is_protected_path(Path("C:\\FakeWindows")))

    def test_anything_inside_windir_is_protected_too(self):
        with mock.patch.dict(os.environ, {"WINDIR": "C:\\FakeWindows"}, clear=False):
            self.assertTrue(is_protected_path(Path("C:\\FakeWindows\\System32\\qualcosa.dll")))

    def test_a_folder_that_merely_starts_with_the_same_prefix_is_not_protected(self):
        """C:\\FakeWindowsToo non e' DENTRO C:\\FakeWindows solo perche' il nome inizia allo
        stesso modo - il controllo deve essere sui genitori veri, non su un prefisso di stringa."""
        with mock.patch.dict(os.environ, {"WINDIR": "C:\\FakeWindows"}, clear=False):
            self.assertFalse(is_protected_path(Path("C:\\FakeWindowsToo")))

    def test_missing_subtree_env_vars_do_not_crash(self):
        # Solo le variabili lette da _subtree_blocked_roots(): rimuoverle non deve sollevare,
        # solo non trovare nessuna radice bloccata da quel lato (home/drive root restano
        # comunque protette, controllate a parte - NON usare clear=True, servirebbe anche a
        # Path.home() per trovare la cartella utente).
        with mock.patch.dict(os.environ, {}, clear=False):
            for var in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
                os.environ.pop(var, None)
            is_protected_path(Path("C:\\qualsiasi\\percorso"))


class OrdinaryPathsAreNotProtectedTests(unittest.TestCase):
    def test_an_ordinary_file_is_not_protected(self):
        self.assertFalse(is_protected_path(Path.home() / "Desktop" / "appunti.txt"))


if __name__ == "__main__":
    unittest.main()
