"""Test per core/computer_use/vscode_adapter.py (F3.7.1, quarta app di F3.7 - mai iniziata prima
d'ora). Lancia DAVVERO VS Code su un file TEMPORANEO isolato (mai un file/cartella reale
dell'utente), con un profilo (`--user-data-dir`/`--extensions-dir`) completamente separato - mai
il profilo reale. Ogni test registra ESPLICITAMENTE quali PID `Code.exe` esistono PRIMA di aprire
qualunque finestra (un utente puo' averne MOLTI, uno per finestra/servizio - verificato 18 in
questa stessa sessione di sviluppo, che gira essa stessa dentro VS Code) e verifica che nessuno
di loro venga mai toccato - una rete di sicurezza nel test stesso, non solo nel codice di
produzione, lo stesso schema gia' seguito per Esplora File (F3.7.1). Saltato esplicitamente se
VS Code non e' installato in questo ambiente (`CodeNotFoundError`), non fatto fallire - stesso
principio gia' seguito per l'OCR (F3.5.1) e il browser (F3.6.1)."""
import shutil
import tempfile
import unittest
from pathlib import Path

import psutil

from core.computer_use.vscode_adapter import (
    CodeNotFoundError,
    close_vscode_window,
    find_code_executable,
    find_vscode_window,
    launch_isolated_vscode,
)
from core.computer_use.ui_automation_adapter import UIAutomationAdapter


def _running_code_pids() -> set[int]:
    return {p.pid for p in psutil.process_iter(["name"]) if p.info.get("name") == "Code.exe"}


def _code_available() -> bool:
    try:
        find_code_executable()
        return True
    except CodeNotFoundError:
        return False


@unittest.skipUnless(_code_available(), "Visual Studio Code non e' installato in questo ambiente")
class RealVSCodeTests(unittest.TestCase):
    def setUp(self):
        # Rete di sicurezza (vedi il docstring del modulo): questi PID - inclusa, se presente,
        # la finestra VS Code che sta eseguendo QUESTA STESSA sessione di sviluppo - non devono
        # MAI essere toccati da questo test.
        self._pre_existing_pids = _running_code_pids()
        self.file_path = Path(tempfile.mkdtemp(prefix="jake_vscode_fixture_")) / "jake_vscode_fixture_file.md"
        self.file_path.write_text("# Titolo di prova\n\nContenuto della fixture VS Code.\n", encoding="utf-8")
        self.adapter = UIAutomationAdapter()
        self.vscode = launch_isolated_vscode(self.file_path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            close_vscode_window(self.adapter, self.file_path.name, timeout_seconds=15.0)
        except Exception:
            pass
        self.vscode.cleanup_temp_dirs()
        shutil.rmtree(self.file_path.parent, ignore_errors=True)
        # Verifica finale di sicurezza, anche se un test fallisce a meta'.
        for pid in self._pre_existing_pids:
            if not psutil.pid_exists(pid):
                self.fail(f"il processo Code.exe pre-esistente {pid} non esiste piu' dopo il test")

    def test_open_find_and_close_a_real_window_without_ever_touching_pre_existing_code_processes(self):
        window = find_vscode_window(self.adapter, self.file_path.name, timeout_seconds=15.0)
        window_pid = window.CurrentProcessId
        self.assertNotIn(
            window_pid, self._pre_existing_pids,
            "la finestra aperta da Jake non deve MAI essere un processo Code.exe gia' esistente (potrebbe essere la sessione reale dell'utente)",
        )

        closed_pid = close_vscode_window(self.adapter, self.file_path.name, timeout_seconds=15.0)

        self.assertEqual(closed_pid, window_pid)
        self.assertFalse(psutil.pid_exists(window_pid), "il processo della finestra deve essere davvero terminato - close_vscode_window attende gia' la conferma")


if __name__ == "__main__":
    unittest.main()
