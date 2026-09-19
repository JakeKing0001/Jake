"""Test per core/computer_use/terminal_adapter.py (F3.7.1, quinta app di F3.7 - mai iniziata
prima d'ora). Lancia DAVVERO `conhost.exe cmd.exe` (mai `cmd.exe`/`start` da soli - vedi il
docstring del modulo per il rischio monarch/peasant di Windows Terminal, scoperto empiricamente e
riprodotto DAVVERO in questo stesso incremento prima di scrivere questo adapter). Ogni test
registra ESPLICITAMENTE quali PID `conhost.exe` esistono PRIMA di aprire qualunque finestra, cosi'
da poter verificare che la PROPRIA finestra non sia mai per errore uno di quei PID gia' esistenti.

**Buco reale trovato eseguendo la suite completa, non ipotizzato - a differenza di Esplora File/VS
Code, QUI la rete di sicurezza "nessun PID pre-esistente deve sparire" e' stata rimossa, non
aggiunta**: `explorer.exe` (il guscio, un solo processo stabile) e `Code.exe` (la sessione VS Code
dello sviluppatore, anch'essa stabile) sono processi di LUNGA vita per costruzione, quindi
verificare che nessun PID pre-esistente sparisca durante il test e' un segnale affidabile per
loro. `conhost.exe` invece si e' rivelato un processo strutturalmente EFFIMERO su questa macchina
(verificato: circa 10 istanze gia' in esecuzione in un momento qualunque, alcune legate a
strumenti/terminali indipendenti da questo test, con tempi di avvio anche di giorni prima) - un
run completo della suite ha fatto fallire il test con "il processo conhost.exe pre-esistente NNN
non esiste piu'" quando quel PID e' semplicemente uscito da solo per un motivo estraneo a questo
test (es. un altro strumento ha chiuso la propria shell), non perche' `close_terminal_window()`
lo avesse mai toccato. Costruire una rete di sicurezza su un segnale che cambia da solo per motivi
indipendenti dal codice sotto test sarebbe stato lo stesso errore gia' evitato per l'Office
adapter (vedi ROADMAP_EXECUTION.md, F3.7.1) - qui pero' scoperto DOPO aver gia' scritto il test,
non prima. La protezione reale resta quella gia' precisa per costruzione in ogni test: il PID
della PROPRIA finestra non deve mai coincidere con uno gia' esistente al via (`setUp`), e
(`test_two_windows_...`) chiudere una finestra propria non deve mai toccare l'ALTRA finestra
propria - entrambe verificate per IDENTITA' di PID, non per un conteggio globale soggetto a
rumore esterno."""
import unittest
import uuid

import psutil

from core.computer_use.terminal_adapter import (
    close_terminal_window,
    describe_terminal_window,
    find_terminal_window,
    launch_isolated_terminal,
    read_terminal_text,
)
from core.computer_use.ui_automation_adapter import UIAutomationAdapter, WindowNotFoundError


def _running_conhost_pids() -> set[int]:
    return {p.pid for p in psutil.process_iter(["name"]) if p.info.get("name") == "conhost.exe"}


class RealTerminalTests(unittest.TestCase):
    def setUp(self):
        # Snapshot usato SOLO per "la mia finestra non deve mai essere uno di questi PID" (vedi
        # il docstring del modulo per perche' l'inverso - "nessuno di questi deve sparire" - e'
        # stato deliberatamente rimosso: un falso positivo reale, non un rischio ipotetico).
        self._pre_existing_pids = _running_conhost_pids()
        self.adapter = UIAutomationAdapter()
        self._titles_to_cleanup: list[str] = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for title in self._titles_to_cleanup:
            try:
                close_terminal_window(self.adapter, title, timeout_seconds=5.0)
            except (WindowNotFoundError, RuntimeError):
                pass

    def _distinctive_title(self, label: str) -> str:
        title = f"jake_terminal_fixture_{label}_{uuid.uuid4().hex[:8]}"
        self._titles_to_cleanup.append(title)
        return title

    def test_open_find_and_close_a_real_window_without_ever_touching_pre_existing_conhost_processes(self):
        title = self._distinctive_title("basic")
        launch_isolated_terminal(title)

        window = find_terminal_window(self.adapter, title, timeout_seconds=10.0)
        window_pid = window.CurrentProcessId
        self.assertNotIn(
            window_pid, self._pre_existing_pids,
            "la finestra aperta da Jake non deve MAI essere un processo conhost.exe gia' esistente",
        )

        closed_pid = close_terminal_window(self.adapter, title, timeout_seconds=10.0)
        self._titles_to_cleanup.remove(title)

        self.assertEqual(closed_pid, window_pid)
        self.assertFalse(psutil.pid_exists(window_pid), "il processo della finestra deve essere davvero terminato")

    def test_two_windows_get_separate_processes_and_closing_one_never_touches_the_other(self):
        """Il test che riproduce DIRETTAMENTE il rischio motivante di questo modulo (vedi il
        docstring): con Windows Terminal, due finestre lanciate in sequenza hanno condiviso lo
        STESSO PID (verificato empiricamente durante l'indagine di questo incremento), e chiudere
        la prima ha distrutto anche la seconda. Questo test verifica che `conhost.exe` diretto
        (usato da `launch_isolated_terminal`) NON abbia questo problema."""
        title_a = self._distinctive_title("a")
        title_b = self._distinctive_title("b")
        launch_isolated_terminal(title_a)
        launch_isolated_terminal(title_b)

        window_a = find_terminal_window(self.adapter, title_a, timeout_seconds=10.0)
        window_b = find_terminal_window(self.adapter, title_b, timeout_seconds=10.0)
        pid_a = window_a.CurrentProcessId
        pid_b = window_b.CurrentProcessId

        self.assertNotEqual(pid_a, pid_b, "due finestre di terminale non devono mai condividere lo stesso processo")

        close_terminal_window(self.adapter, title_a, timeout_seconds=10.0)
        self._titles_to_cleanup.remove(title_a)

        # B deve essere ancora viva e trovabile: la prova diretta che chiudere A non l'ha toccata.
        still_b = find_terminal_window(self.adapter, title_b, timeout_seconds=5.0)
        self.assertEqual(still_b.CurrentProcessId, pid_b)
        self.assertTrue(psutil.pid_exists(pid_b))

    def test_read_terminal_text_reads_the_real_buffer_content_not_just_the_title(self):
        title = self._distinctive_title("text")
        marker = f"MARCATORE_{uuid.uuid4().hex[:12]}"
        launch_isolated_terminal(title, initial_command=f"echo {marker}")

        window = find_terminal_window(self.adapter, title, timeout_seconds=10.0)
        text = read_terminal_text(self.adapter, window)

        self.assertIn(marker, text, "il buffer letto deve contenere l'output reale del comando, non solo il titolo")

    def test_describe_terminal_window_produces_a_real_structured_dump_not_an_empty_one(self):
        """F3.2 (criterio di uscita - "dump semantico stabile di cinque app reali"): a differenza
        di VS Code (`tests/test_vscode_adapter.py`, un incremento successivo ha trovato che
        `describe_tree` trova quasi nulla li'), `conhost.exe` e' un host di console LEGACY con un
        provider di accessibilita' nativo completo - il dump deve contenere elementi REALI
        (ScrollBar/Document), non solo la finestra vuota."""
        title = self._distinctive_title("dump")
        launch_isolated_terminal(title)

        window = find_terminal_window(self.adapter, title, timeout_seconds=10.0)
        tree = describe_terminal_window(self.adapter, window)

        control_types = {child.control_type for child in tree.children}
        self.assertIn("ScrollBar", control_types)
        self.assertIn("Document", control_types)


if __name__ == "__main__":
    unittest.main()
