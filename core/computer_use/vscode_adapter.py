"""VS Code adapter (F3.7.1, quarta app di F3.7 nell'ordine dichiarato "Esplora file ->
Impostazioni -> browser -> VS Code -> terminale -> Office -> media -> messaggistica" - Impostazioni
saltata per questo incremento, vedi sotto - mai iniziata prima d'ora, vedi ROADMAP_EXECUTION.md
sezione F3.7, dipende da F3.4). Apre/localizza/chiude una finestra di VS Code REALE tramite UI
Automation (F3.2) - stesso principio "riusa l'infrastruttura gia' costruita" gia' seguito per
Esplora File (F3.7.1) e il browser (F3.6), nessuna nuova libreria. VS Code e' anch'esso un'app
Electron/Chromium (come Edge, F3.6) - non a caso condivide con esso lo stesso genere di
comportamenti gia' trovati li'.

**"Impostazioni" (l'app dichiarata SUBITO dopo Esplora File nell'ordine di F3.7) DELIBERATAMENTE
SALTATA in questo incremento - un rischio verificato, non ipotizzato**: a differenza di Esplora
File (dove aprire una nuova finestra crea sempre un processo separato), l'app Impostazioni di
Windows e' un'app UWP SINGLE-INSTANCE per utente - verificato che l'utente ha GIA' una finestra
Impostazioni aperta (un processo `SystemSettings.exe` gia' in esecuzione PRIMA di questo
incremento) - non esiste un modo verificato per aprirne una copia ISOLATA come per Edge
(`--inprivate`) o VS Code (`--user-data-dir`, vedi sotto): automatizzarla ora rischierebbe di
interagire con la finestra Impostazioni REALE gia' aperta dall'utente, non una isolata. Rimandato
a un incremento futuro che verifichi prima un modo sicuro di isolarla.

**Isolamento VERIFICATO PRIMA di scrivere qualunque test, non assunto**: VS Code accetta
`--user-data-dir`/`--extensions-dir` PROPRI (mai il profilo reale dell'utente, mai le estensioni
reali) esattamente come `--inprivate` isola Edge (F3.6.1) - verificato lanciando una finestra
isolata mentre QUESTA STESSA sessione Claude Code girava dentro un'altra finestra VS Code
("Roadmap - Jake - Visual Studio Code", il rischio piu' concreto possibile per questo incremento):
il PID della finestra isolata e' risultato SEPARATO, e terminarlo ha lasciato la finestra della
sessione reale (e tutti gli altri 18 processi Code gia' in esecuzione) del tutto intatti,
verificato leggendo il conteggio dei processi prima e dopo.

**Buco reale trovato aprendo un file specifico invece di una cartella, non ipotizzato**: il
titolo della finestra NON contiene il nome della cartella se si apre un singolo FILE (solo
"nomefile.md - Visual Studio Code", niente cartella) - a differenza di Esplora File, dove il nome
della cartella e' sempre nel titolo. `find_vscode_window()` cerca quindi per il NOME DEL FILE
(deliberatamente distintivo, non "prova.md" generico che potrebbe corrispondere a un file gia'
aperto dall'utente), non per la cartella.

**Buco reale piu' importante di questo incremento, gia' noto a VS Code stesso - non un limite di
questo codice**: l'editor Monaco (il componente che mostra il codice) dichiara esplicitamente,
LEGGIBILE via UI Automation, "The editor is not accessible at this time. To enable screen reader
optimized mode..." - il CONTENUTO del file aperto NON e' esposto come testo via UI Automation per
default (un'ottimizzazione deliberata delle prestazioni di VS Code, non un buco del ponte di
accessibilita' come per Qt in F3.4). Leggere il contenuto REALE di un file aperto in VS Code
richiederebbe o abilitare `editor.accessibilitySupport` nel profilo isolato (non affrontato qui)
o leggere il file direttamente dal filesystem (che rinuncerebbe al punto di "osservare cio' che
e' mostrato sullo schermo") - dichiarato onestamente NON RISOLTO, non nascosto.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- Impostazioni (vedi sopra, rimandata per un rischio di sicurezza verificato);
- lettura del contenuto reale dell'editor (vedi il buco Monaco sopra);
- gestione del dialogo "Welcome to Visual Studio Code" (richiesta di accesso GitHub Copilot) -
  osservato comparire anche con un profilo isolato e `--disable-workspace-trust`, non ancora
  chiuso/gestito automaticamente da questo modulo;
- qualunque azione (digitare, salvare, eseguire un comando) - solo apertura/localizzazione/
  chiusura in questa prima fetta."""
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from core.computer_use.ui_automation_adapter import UIAutomationAdapter

_CANDIDATE_CODE_PATHS = [
    Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd",
    Path(r"C:\Program Files\Microsoft VS Code\bin\code.cmd"),
]


class CodeNotFoundError(Exception):
    """Nessun eseguibile `code.cmd` trovato in nessuno dei percorsi noti."""


def find_code_executable() -> Path:
    """Onesto `CodeNotFoundError` (non un percorso indovinato) se VS Code non e' installato in
    nessuno dei percorsi standard di un'installazione Windows - stesso principio gia' seguito da
    `browser_adapter.py::find_edge_executable` (F3.6.1)."""
    for candidate in _CANDIDATE_CODE_PATHS:
        if candidate.is_file():
            return candidate
    raise CodeNotFoundError(f"nessun eseguibile VS Code trovato in {_CANDIDATE_CODE_PATHS}")


class IsolatedVSCodeProcess:
    """Il processo VS Code lanciato da `launch_isolated_vscode` PIU' i percorsi dei due profili
    temporanei creati per lui (profilo utente ED estensioni, entrambi isolati) - stesso principio
    gia' seguito da `browser_adapter.py::IsolatedBrowserProcess` (F3.6.1) per evitare di
    accumulare profili orfani mai ripuliti."""

    def __init__(self, launcher: subprocess.Popen, user_data_dir: str, extensions_dir: str) -> None:
        self.launcher = launcher
        self.user_data_dir = user_data_dir
        self.extensions_dir = extensions_dir

    def cleanup_temp_dirs(self) -> None:
        """Cancella i due profili temporanei - il CHIAMANTE deve prima chiudere la finestra reale
        tramite `close_vscode_window()` (che trova e termina il PID vero, diverso da quello del
        launcher - vedi il docstring del modulo), cancellare i profili mentre VS Code li usa
        ancora fallirebbe silenziosamente su Windows (file bloccati dal processo)."""
        shutil.rmtree(self.user_data_dir, ignore_errors=True)
        shutil.rmtree(self.extensions_dir, ignore_errors=True)


def launch_isolated_vscode(path: Path) -> IsolatedVSCodeProcess:
    """Lancia VS Code su `path` (un file o una cartella) con un profilo temporaneo ISOLATO (mai
    il profilo/le estensioni reali dell'utente - vedi il docstring del modulo per la verifica di
    sicurezza gia' fatta) - il chiamante e' responsabile di chiudere la finestra reale tramite
    `close_vscode_window()` e poi ripulire tramite `.cleanup_temp_dirs()`.

    Attende che il PROCESSO LANCIATO (`code.cmd`, un batch che passa la richiesta al vero VS Code
    e poi esce da solo, un launcher a se' - stesso schema gia' trovato per `explorer.exe`, F3.7.1)
    esca da solo, solo per evitare un `ResourceWarning` di Python su un `Popen` mai raccolto, non
    per sincronizzarsi con la finestra vera (per quello serve `find_vscode_window`)."""
    executable = find_code_executable()
    user_data_dir = tempfile.mkdtemp(prefix="jake_vscode_userdata_")
    extensions_dir = tempfile.mkdtemp(prefix="jake_vscode_ext_")
    launcher = subprocess.Popen([
        str(executable), "--new-window", f"--user-data-dir={user_data_dir}",
        f"--extensions-dir={extensions_dir}", "--disable-workspace-trust", str(path),
    ], shell=True)
    try:
        launcher.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        pass
    return IsolatedVSCodeProcess(launcher, user_data_dir, extensions_dir)


def find_vscode_window(adapter: UIAutomationAdapter, distinctive_name: str, timeout_seconds: float = 15.0):
    """Trova la finestra VS Code il cui titolo contiene `distinctive_name` - per SOTTOSTRINGA
    (`find_window_by_title_containing`, F3.7), mai per uguaglianza esatta (il titolo completo
    include sempre "- Visual Studio Code", e per un file senza cartella aperta NON include il
    percorso, solo il nome del file - vedi il docstring del modulo). `distinctive_name` deve
    essere scelto dal chiamante abbastanza specifico da non corrispondere a un file/cartella
    gia' aperti dall'utente - un'ambiguita' solleva `AmbiguousWindowError` invece di scegliere a
    caso, lo stesso principio gia' seguito per Esplora File."""
    return adapter.find_window_by_title_containing(distinctive_name, timeout_seconds=timeout_seconds)


def close_vscode_window(adapter: UIAutomationAdapter, distinctive_name: str, timeout_seconds: float = 15.0) -> int:
    """Chiude SOLO la finestra VS Code trovata per `distinctive_name` - mai per nome processo
    "Code" (che con questa app e' particolarmente pericoloso: un utente ha tipicamente MOLTI
    processi Code.exe insieme, uno per finestra/servizio, verificato 18 in questa stessa sessione
    - terminare per nome colpirebbe anche la finestra REALE dell'utente). Il PID da terminare
    viene letto DALLA FINESTRA stessa (`CurrentProcessId`), mai passato o indovinato dal
    chiamante. Attende la terminazione VERA prima di restituirsi (stesso principio "verificare
    l'effetto" gia' seguito da `file_explorer_adapter.py::close_explorer_window`, F3.7.1) e
    restituisce il PID chiuso."""
    window = find_vscode_window(adapter, distinctive_name, timeout_seconds=timeout_seconds)
    pid = window.CurrentProcessId
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)

    deadline = time.monotonic() + timeout_seconds
    while _process_exists(pid):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"il processo VS Code {pid} risulta ancora vivo dopo {timeout_seconds}s")
        time.sleep(0.1)
    return pid


def _process_exists(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False,
    )
    return str(pid) in result.stdout
