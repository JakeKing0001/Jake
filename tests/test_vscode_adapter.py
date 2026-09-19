"""Test per core/computer_use/vscode_adapter.py (F3.7.1, quarta app di F3.7 - mai iniziata prima
d'ora). Lancia DAVVERO VS Code su un file TEMPORANEO isolato (mai un file/cartella reale
dell'utente), con un profilo (`--user-data-dir`/`--extensions-dir`) completamente separato - mai
il profilo reale. Ogni test registra ESPLICITAMENTE quali PID `Code.exe` esistono PRIMA di aprire
qualunque finestra (un utente puo' averne MOLTI, uno per finestra/servizio - verificato 18 in
questa stessa sessione di sviluppo, che gira essa stessa dentro VS Code), cosi' da poter
verificare che la PROPRIA finestra non sia mai per errore uno di quei PID gia' esistenti.

**Buco reale trovato eseguendo la suite completa in un incremento successivo, non ipotizzato -
stessa CONSEGUENZA gia' vista per `conhost.exe` (`tests/test_terminal_adapter.py`), ma una causa
DIVERSA**: la rete di sicurezza "nessun PID Code.exe pre-esistente deve sparire" ha fatto fallire
il test con "il processo Code.exe pre-esistente NNN non esiste piu'" - non perche'
`close_vscode_window()` avesse toccato la finestra sbagliata (la sessione VS Code reale di questa
macchina e' rimasta intatta, verificato), ma perche' VS Code (un'app Electron multi-processo) fa
nascere/terminare DA SOLO processi `Code.exe` AUSILIARI (utility process, GPU process, extension
host) come normale funzionamento interno, ANCHE quando la sessione principale resta aperta e
stabile per tutta la durata del test - a differenza di `explorer.exe` (un solo processo stabile
per il guscio), il NUMERO di processi `Code.exe` di una sessione gia' aperta puo' variare da solo
nel tempo. Rimossa la stessa singola asserzione globale (per lo stesso motivo gia' documentato per
il terminale: un segnale che cambia da solo per motivi indipendenti dal codice sotto test non e'
una rete di sicurezza affidabile), mantenuta la verifica gia' precisa per IDENTITA' di PID (la
finestra aperta da Jake non e' mai uno dei PID gia' esistenti). Saltato esplicitamente se VS Code
non e' installato in questo ambiente (`CodeNotFoundError`), non fatto fallire - stesso principio
gia' seguito per l'OCR (F3.5.1) e il browser (F3.6.1)."""
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
        # Snapshot usato SOLO per "la mia finestra non deve mai essere uno di questi PID" (vedi
        # il docstring del modulo per perche' l'inverso - "nessuno di questi deve sparire" - e'
        # stato deliberatamente rimosso: un falso positivo reale, non un rischio ipotetico).
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

    def test_describe_tree_finds_almost_nothing_a_known_limitation_not_a_silent_regression(self):
        """Buco reale trovato in un incremento successivo (vedi il docstring del modulo): l'INTERA
        UI di VS Code, non solo l'editor Monaco, e' quasi del tutto invisibile al "control view" di
        UI Automation - un dump a profondita' 20 trova solo la finestra/un pannello/i tre bottoni
        del chrome nativo, nessuna voce di menu ne' scheda. Questo test codifica ESATTAMENTE il
        comportamento oggi osservato come un CANARINO, non un risultato ignorato (stesso principio
        gia' seguito da `ExpandCollapseKnownLimitationTests`, F3.4): se una futura versione di VS
        Code migliorasse il supporto accessibilita' per default, questo test fallirebbe (trovando
        PIU' di 5 elementi con nome) e andrebbe aggiornato, invece di lasciare che il limite resti
        silenziosamente sotto-documentato per sempre."""
        window = find_vscode_window(self.adapter, self.file_path.name, timeout_seconds=15.0)

        tree = self.adapter.describe_tree(window, max_depth=20)
        named = []

        def collect(node):
            if node.name:
                named.append(node.name)
            for child in node.children:
                collect(child)

        collect(tree)

        self.assertLessEqual(
            len(named), 6,
            "se questo fallisce, l'accessibilita' di VS Code e' probabilmente migliorata - "
            "aggiornare questo test E valutare se costruire un describe_vscode_window() reale",
        )


if __name__ == "__main__":
    unittest.main()
