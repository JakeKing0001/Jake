"""Terminal adapter (F3.7.1, quinta app di F3.7 nell'ordine dichiarato "Esplora file ->
Impostazioni -> browser -> VS Code -> terminale -> Office -> media -> messaggistica" -
Impostazioni ancora saltata, vedi vscode_adapter.py - mai iniziata prima d'ora, vedi
ROADMAP_EXECUTION.md sezione F3.7, dipende da F3.4). Apre/localizza/legge/chiude una finestra di
terminale REALE tramite UI Automation (F3.2) - stesso principio "riusa l'infrastruttura gia'
costruita" di Esplora File e VS Code, nessuna nuova libreria.

**Rischio di sicurezza REALE, PIU' insidioso di quello di Esplora File - scoperto da un SECONDO
probe empirico, non dal primo**: Windows Terminal (l'app predefinita per aprire una console su
Windows 11, verificata essere il default su questa macchina) usa un'architettura "monarch/peasant"
- PIU' finestre lanciate con `start "titolo" cmd.exe` in sequenza possono condividere lo STESSO
processo sottostante (verificato: due finestre lanciate separatamente, con titoli diversi,
riportavano lo STESSO `CurrentProcessId` - un fatto INVISIBILE lanciando una sola finestra alla
volta, il primo probe di questo incremento sembrava innocuo). La conseguenza e' stata riprodotta
DAVVERO, non solo temuta: terminare quel PID condiviso (con l'intento di chiudere solo la prima
finestra) ha chiuso ANCHE la seconda finestra, completamente indipendente agli occhi dell'utente.
Applicare qui lo stesso schema gia' usato per Esplora File/VS Code (`taskkill` sul PID della
finestra trovata) rischierebbe quindi di chiudere finestre/schede REALI dell'utente che condividono
lo stesso processo Windows Terminal, non solo quella creata da Jake.

**Soluzione VERIFICATA, non assunta**: invocare `conhost.exe` (l'host di console legacy, ancora
presente e funzionante su Windows 11 anche con Windows Terminal impostato come predefinito)
DIRETTAMENTE al posto di lasciare che il sistema apra Windows Terminal, bypassando cosi'
l'architettura monarch/peasant. Verificato con lo stesso genere di probe a due finestre che ha
trovato il rischio sopra: due finestre `conhost.exe cmd.exe` lanciate in sequenza hanno riportato
DUE PID DIVERSI, e terminare il PID della prima ha lasciato la seconda intatta e ancora trovabile
per titolo - lo stesso identico test "apri due finestre, verifica PID diversi, chiudi una, verifica
che l'altra sopravviva" gia' richiesto per Esplora File, qui superato da `conhost.exe` e FALLITO da
Windows Terminal. Questo adapter lancia quindi sempre `conhost.exe cmd.exe`, mai `cmd.exe` da solo
(che su questa macchina passerebbe comunque per Windows Terminal) e mai `wt.exe` esplicitamente.

**Buco reale trovato per il TITOLO, coerente con Esplora File/VS Code**: `cmd.exe` non accetta un
titolo finestra come argomento diretto - verificato che serve il comando interno `title` eseguito
DENTRO la shell (`cmd.exe /k "title <nome> && <comando>"`). Il chiamante deve quindi scegliere un
`distinctive_title` abbastanza specifico da non corrispondere a nessuna finestra gia' aperta
dall'utente, esattamente come `distinctive_name` per VS Code - un'ambiguita' solleva
`AmbiguousWindowError` (F3.7) invece di scegliere a caso.

**Capacita' in piu' rispetto a Esplora File/VS Code, verificata con un probe dedicato invece di
assunta impossibile**: a differenza dell'editor Monaco di VS Code (dichiarato "non accessibile" per
default, vedi vscode_adapter.py), il buffer di testo REALE di un `conhost.exe` E' leggibile via UI
Automation - verificato interrogando l'elemento `Document` ("Text Area") con `TextPattern`
(`IUIAutomationTextPattern.DocumentRange.GetText(-1)`), che ha restituito il contenuto VERO
stampato nella shell, non solo il titolo della finestra. `read_terminal_text()` espone questa
capacita' - una differenza reale tra due app della stessa famiglia "F3.7 adapter", non un'assunzione
che tutte le app si comportino allo stesso modo.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- inviare input alla shell (digitare un comando ed eseguirlo) - solo apertura/localizzazione/
  lettura/chiusura in questa prima fetta, stesso schema di Esplora File/VS Code;
- Windows Terminal stesso (con le sue schede/pannelli multipli) resta NON supportato da questo
  modulo per il rischio monarch/peasant sopra - un incremento futuro potrebbe rivalutarlo se si
  trova un modo verificato di distinguere un PID "sicuro da terminare" da uno condiviso (es.
  contare le finestre di primo livello che condividono lo stesso PID prima di terminarlo), non
  affrontato qui;
- `conhost.exe` in esecuzione sotto altri host (es. PowerShell invece di `cmd.exe`) non ancora
  verificato, solo `cmd.exe` usato in questo incremento."""
import subprocess
import time

import comtypes
from comtypes.gen import UIAutomationClient as UIA

from core.computer_use.ui_automation_adapter import UIAutomationAdapter


def launch_isolated_terminal(distinctive_title: str, initial_command: str | None = None) -> None:
    """Lancia `conhost.exe cmd.exe` (mai `cmd.exe`/`start` da soli - vedi il docstring del modulo
    per il rischio monarch/peasant di Windows Terminal che questo evita) con un titolo impostato
    DENTRO la shell tramite il comando `title` (l'unico modo verificato, `cmd.exe` non accetta un
    titolo come argomento). `initial_command`, se dato, viene eseguito subito dopo (utile solo per
    verificare che il buffer sia leggibile - vedi `read_terminal_text` - non affrontato oltre in
    questo incremento). Non restituisce un oggetto processo utilizzabile per la terminazione, come
    gia' per Esplora File: il chiamante deve localizzare/chiudere tramite `find_terminal_window`/
    `close_terminal_window`, che trovano il PID vero, mai indovinato.

    **Nessun `launcher.wait(...)` qui, a differenza di Esplora File/VS Code - una differenza
    DELIBERATA, non un'omissione**: per quei due, il processo lanciato e' un vero LAUNCHER che
    passa la richiesta al processo reale della finestra e poi esce da solo in fretta (verificato).
    Qui invece il processo lanciato (`conhost.exe`) verificato COINCIDE con il processo reale della
    finestra (stesso PID, vedi il docstring del modulo) - non esce finche' la finestra non viene
    chiusa da `close_terminal_window`. Chiamare `.wait(timeout=...)` fingerebbe quindi un'uscita
    imminente che non arrivera' mai, sprecando l'intero timeout ad ogni chiamata per nessun
    beneficio reale. Un `ResourceWarning` su questo `Popen` scartato e' quindi un effetto
    collaterale ATTESO e innocuo di questa scelta (l'oggetto Python viene raccolto mentre il
    processo che rappresenta e' ancora vivo per design, non per un bug), non nascosto qui ma
    dichiarato onestamente invece di inseguito con un `wait()` che mentirebbe sul comportamento
    reale del processo."""
    shell_command = f"title {distinctive_title}"
    if initial_command:
        shell_command += f" && {initial_command}"
    subprocess.Popen(["conhost.exe", "cmd.exe", "/k", shell_command])


def find_terminal_window(adapter: UIAutomationAdapter, distinctive_title: str, timeout_seconds: float = 10.0):
    """Trova la finestra di terminale il cui titolo contiene `distinctive_title` - per
    SOTTOSTRINGA (`find_window_by_title_containing`, F3.7), mai per uguaglianza esatta: il titolo
    completo di un `conhost.exe` include uno spazio finale e puo' includere altro testo (verificato
    nel probe di questo incremento: `'jake_conhost_text_probe '`, con uno spazio finale non scelto
    dal chiamante)."""
    return adapter.find_window_by_title_containing(distinctive_title, timeout_seconds=timeout_seconds)


def close_terminal_window(adapter: UIAutomationAdapter, distinctive_title: str, timeout_seconds: float = 10.0) -> int:
    """Chiude SOLO la finestra trovata per `distinctive_title` - il PID viene letto DALLA FINESTRA
    stessa (`CurrentProcessId`), mai passato o indovinato dal chiamante, stesso schema di Esplora
    File/VS Code. Sicuro DAVVERO solo perche' la finestra e' stata aperta da `launch_isolated_terminal`
    (che usa sempre `conhost.exe` diretto, mai Windows Terminal - vedi il docstring del modulo):
    terminare questo PID non puo' toccare altre finestre, a differenza del PID condiviso di Windows
    Terminal. Attende la terminazione VERA prima di restituirsi (stesso principio "verificare
    l'effetto" gia' seguito da `file_explorer_adapter.py`/`vscode_adapter.py`) e restituisce il PID
    chiuso."""
    window = find_terminal_window(adapter, distinctive_title, timeout_seconds=timeout_seconds)
    pid = window.CurrentProcessId
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)

    deadline = time.monotonic() + timeout_seconds
    while _process_exists(pid):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"il processo terminale {pid} risulta ancora vivo dopo {timeout_seconds}s")
        time.sleep(0.1)
    return pid


def _process_exists(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False,
    )
    return str(pid) in result.stdout


class TerminalTextNotFoundError(Exception):
    """Nessun elemento `Document` (il "Text Area" del buffer di console, vedi il docstring del
    modulo) trovato nella finestra, o quello trovato non supporta `TextPattern` - onesto invece di
    restituire una stringa vuota che un chiamante potrebbe confondere con "il buffer e' davvero
    vuoto"."""


def read_terminal_text(adapter: UIAutomationAdapter, window) -> str:
    """F3.7.1 (capacita' in piu' di questo adapter rispetto a Esplora File/VS Code - vedi il
    docstring del modulo): il contenuto VERO del buffer di console, letto tramite `TextPattern`
    sull'elemento `Document` della finestra - non solo il titolo o i nomi dei controlli di
    contorno (scrollbar, barra del titolo) gia' esposti da `describe_tree`."""
    documents = adapter.find_matching_elements(window, control_type="Document")
    if not documents:
        raise TerminalTextNotFoundError("nessun elemento Document (Text Area) trovato nella finestra")
    document = documents[0]
    try:
        pattern = document.GetCurrentPattern(UIA.UIA_TextPatternId)
        if not pattern:
            raise TerminalTextNotFoundError("l'elemento Document non supporta TextPattern")
        text_pattern = pattern.QueryInterface(UIA.IUIAutomationTextPattern)
        return text_pattern.DocumentRange.GetText(-1)
    except (ValueError, comtypes.COMError) as exc:
        raise TerminalTextNotFoundError(f"lettura TextPattern fallita: {exc}") from exc


def describe_terminal_window(adapter: UIAutomationAdapter, window):
    """F3.2 (criterio di uscita - "dump semantico stabile... di cinque app reali supportate"):
    un semplice passaggio a `UIAutomationAdapter.describe_tree` - nessuna logica in piu', il
    valore di questa funzione e' la PROVA (vedi `tests/test_terminal_adapter.py`) che il dump
    prodotto per `conhost.exe` e' davvero STABILE e UTILE (contiene `ScrollBar`/`MenuBar`/
    `Document`, gli stessi elementi reali gia' usati da `read_terminal_text`), a differenza di
    VS Code (F3.7.1 - un incremento successivo ha trovato che l'INTERA UI di VS Code, non solo
    l'editor Monaco, e' quasi del tutto invisibile allo stesso `describe_tree`, vedi il docstring
    di `vscode_adapter.py` - deliberatamente NESSUNA funzione equivalente offerta li', per non
    spedire una capacita' che produrrebbe quasi sempre un albero vuoto). `conhost.exe` e' un host
    di console LEGACY (non Electron/Chromium) con un provider di accessibilita' nativo completo -
    la stessa differenza gia' vista tra Esplora File (controlli Win32 nativi, dump completo) e
    VS Code (UI custom-disegnata, dump quasi vuoto)."""
    return adapter.describe_tree(window, max_depth=10)
