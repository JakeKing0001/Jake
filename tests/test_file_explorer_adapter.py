"""Test per core/computer_use/file_explorer_adapter.py (F3.7.1, prima fetta di F3.7 - "Adapter
applicativi", primo nell'ordine dichiarato - mai iniziata prima d'ora). Lancia DAVVERO Esplora
File su una cartella TEMPORANEA isolata (mai una cartella reale dell'utente come Desktop/
Documenti) - vedi il docstring del modulo per il rischio di sicurezza gia' verificato:
`explorer.exe` e' anche il processo SHELL di Windows, un solo processo di norma. Ogni test
registra ESPLICITAMENTE quali PID `explorer.exe` esistono PRIMA di aprire qualunque finestra e
verifica che nessuno di loro venga mai toccato - una rete di sicurezza nel test stesso, non solo
nel codice di produzione."""
import shutil
import tempfile
import unittest
from pathlib import Path

import psutil

from core.computer_use.file_explorer_adapter import (
    close_explorer_window,
    find_explorer_window,
    list_files,
    open_explorer_window,
)
from core.computer_use.ui_automation_adapter import UIAutomationAdapter, WindowNotFoundError


def _running_explorer_pids() -> set[int]:
    return {p.pid for p in psutil.process_iter(["name"]) if p.info.get("name") == "explorer.exe"}


class RealFileExplorerTests(unittest.TestCase):
    def setUp(self):
        # Rete di sicurezza (vedi il docstring del modulo): questi PID - tipicamente il guscio
        # di Windows, ma anche qualunque altra finestra di Esplora File gia' aperta dall'utente -
        # non devono MAI essere toccati da questo test.
        self._pre_existing_pids = _running_explorer_pids()
        self.folder = Path(tempfile.mkdtemp(prefix="jake_explorer_fixture_"))
        (self.folder / "documento1.txt").write_text("contenuto di prova", encoding="utf-8")
        (self.folder / "documento2.txt").write_text("altro contenuto", encoding="utf-8")
        self.adapter = UIAutomationAdapter()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            close_explorer_window(self.adapter, self.folder.name, timeout_seconds=2.0)
        except WindowNotFoundError:
            pass
        shutil.rmtree(self.folder, ignore_errors=True)
        # Verifica finale di sicurezza, anche se un test fallisce a meta': nessun processo
        # pre-esistente deve essere sparito.
        for pid in self._pre_existing_pids:
            if not psutil.pid_exists(pid):
                self.fail(f"il processo explorer.exe pre-esistente {pid} non esiste piu' dopo il test - possibile guscio colpito per errore")

    def test_open_find_and_close_a_real_window_without_ever_touching_pre_existing_explorer_processes(self):
        open_explorer_window(self.folder)

        window = find_explorer_window(self.adapter, self.folder.name, timeout_seconds=10.0)
        window_pid = window.CurrentProcessId
        self.assertNotIn(
            window_pid, self._pre_existing_pids,
            "la finestra aperta da Jake non deve MAI essere un processo explorer.exe gia' esistente (potrebbe essere il guscio)",
        )

        closed_pid = close_explorer_window(self.adapter, self.folder.name, timeout_seconds=5.0)

        self.assertEqual(closed_pid, window_pid)
        self.assertFalse(psutil.pid_exists(window_pid), "il processo della finestra deve essere davvero terminato - close_explorer_window attende gia' la conferma")

    def test_list_files_reads_the_real_folder_contents_not_the_whole_window(self):
        open_explorer_window(self.folder)
        window = find_explorer_window(self.adapter, self.folder.name, timeout_seconds=10.0)

        files = list_files(self.adapter, window)

        self.assertEqual(sorted(files), ["documento1.txt", "documento2.txt"])


if __name__ == "__main__":
    unittest.main()
