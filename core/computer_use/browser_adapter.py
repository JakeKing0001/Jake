"""Browser adapter (F3.6.1/F3.6.2, prima fetta di F3.6 - "Browser adapter", mai iniziata prima
d'ora - vedi ROADMAP_EXECUTION.md sezione F3.6, dipende da F1.5 e F3.5). Legge il contenuto di una
pagina web REALE senza una libreria di automazione browser nuova (Selenium/Playwright/Chrome
DevTools Protocol) - un browser Chromium espone gia' la propria struttura DOM come un vero albero
di UI Automation, lo stesso meccanismo che permette a uno screen reader (NVDA/JAWS) di leggere una
pagina. Riusa per intero l'infrastruttura gia' costruita in F3.2-F3.5
(`UIAutomationAdapter`/`SelectorEngine`), non ne duplica la logica COM.

**Buco reale trovato lanciando davvero Edge contro una pagina fixture locale, non ipotizzato**:
Chromium NON espone la propria struttura DOM come albero di UI Automation SUBITO al lancio -
l'albero di accessibilita' resta "addormentato" (solo il chrome del browser e' visibile: barra
degli indirizzi, tab, bottoni; il contenuto della pagina - anche gia' completamente caricato - e'
assente, verificato con una `describe_tree` immediata che non mostra affatto il nodo `Document`)
finche' un client UI Automation non lo "sveglia" con una prima interrogazione - da quel momento
resta visibile per il resto della sessione del browser. `find_page_document` FA anche da sveglia,
non solo da selettore: la stessa chiamata che isola il contenuto pagina dal chrome del browser
(F3.6.2) e' anche l'unico modo per farlo comparire la prima volta.

**Secondo buco reale trovato nella stessa indagine - un rischio di privacy concreto, non
teorico**: lanciare Edge con un `--user-data-dir` vuoto/nuovo (l'isolamento normalmente
sufficiente altrove in questa sessione) NON basta a evitare che il browser si colleghi comunque
all'account Microsoft REALE collegato a Windows - verificato: un dialogo di sincronizzazione del
profilo e' comparso mostrando l'indirizzo email vero dell'utente durante l'indagine (mai salvato
ne' mostrato oltre la finestra di debug locale di quella sessione, il processo e' stato terminato
subito). `launch_isolated_browser` usa quindi flag ESPLICITI di isolamento (`--inprivate
--disable-sync --disable-features=...`), non solo un profilo vuoto - qualunque futuro codice che
lanci un browser reale per Jake DEVE passare da qui, non reinventare l'isolamento.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- F3.6.2 (resto): un vocabolario/euristica per "istruzioni dell'utente" dentro la pagina (oggi
  distingue solo chrome del browser da contenuto pagina, non contenuto pagina genuino da testo
  che TENTA di sembrare un'istruzione per l'agente);
- F3.6.3 (form/tab/download/upload con policy specifica - oggi solo lettura, nessuna azione);
- F3.6.4 (collegamento a `core/taint.py::EXTERNAL_CONTENT_INTENTS` - nessun intent/skill ancora
  legge testo di pagina, quindi non c'e' ancora un punto di produzione a cui collegarsi, lo stesso
  principio gia' seguito da F1.5.1 per introdurre un pezzo alla volta);
- F3.6.5 (verificare URL/stato controllo/risposta del sito);
- F3.6.6 (rispettare CAPTCHA/login/protezioni anti-automazione - `launch_isolated_browser` non
  tenta mai login automatico, ma non c'e' ancora una policy esplicita che lo vieti);
- F3.6.7 (redigere password/campi sensibili - nessun campo password ancora letto da questo
  modulo);
- trovare l'eseguibile del browser SOLO su Edge, un percorso fisso (`_CANDIDATE_EDGE_PATHS`) -
  Chrome/Firefox non ancora supportati, ne' un rilevamento piu' robusto del browser predefinito
  dell'utente."""
import subprocess
import tempfile
from pathlib import Path

from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter

# F3.6.1 (isolamento, buco reale trovato - vedi il docstring del modulo): un --user-data-dir
# nuovo da solo non basta a evitare che Edge si colleghi all'account Microsoft reale gia'
# collegato a Windows - servono questi flag espliciti.
_EDGE_ISOLATION_ARGS = [
    "--inprivate", "--disable-sync", "--no-first-run", "--no-default-browser-check",
    "--disable-features=msEdgeAccountLinking,MicrosoftEdgeSyncSurface,msSignoutOnSessionRevoked",
]

_CANDIDATE_EDGE_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


class BrowserNotFoundError(Exception):
    """Nessun eseguibile Edge trovato in nessuno dei percorsi noti."""


def find_edge_executable() -> Path:
    """Onesto `BrowserNotFoundError` (non un percorso indovinato) se Edge non e' installato in
    nessuno dei due percorsi standard di un'installazione Windows."""
    for candidate in _CANDIDATE_EDGE_PATHS:
        if candidate.is_file():
            return candidate
    raise BrowserNotFoundError(f"nessun eseguibile Edge trovato in {_CANDIDATE_EDGE_PATHS}")


class IsolatedBrowserProcess:
    """Il processo Edge lanciato da `launch_isolated_browser` PIU' il percorso del profilo
    temporaneo creato per lui - un `subprocess.Popen` da solo non lo esporrebbe, e senza di
    esso il chiamante non potrebbe mai ripulirlo (buco reale trovato eseguendo i test di questo
    modulo piu' volte, non ipotizzato: 19 profili temporanei vuoti accumulati in `%TEMP%` dopo
    poche esecuzioni, mai cancellati perche' nessuno aveva un riferimento al percorso)."""

    def __init__(self, process: subprocess.Popen, user_data_dir: str) -> None:
        self.process = process
        self.user_data_dir = user_data_dir

    def terminate_and_cleanup(self, timeout_seconds: float = 5.0) -> None:
        """Termina il processo (con `.kill()` di ripiego se non esce entro il timeout, stesso
        schema gia' seguito da ogni test di questa sessione) POI cancella il profilo temporaneo -
        mai l'inverso, cancellare un profilo ancora in uso da Edge fallirebbe silenziosamente su
        Windows (file bloccati dal processo)."""
        import shutil

        self.process.terminate()
        try:
            self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        shutil.rmtree(self.user_data_dir, ignore_errors=True)


def launch_isolated_browser(url: str) -> IsolatedBrowserProcess:
    """Lancia Edge su `url` con un profilo temporaneo ISOLATO (mai il profilo reale dell'utente,
    vedi il docstring del modulo) - il chiamante e' responsabile di chiamare
    `.terminate_and_cleanup()` sul risultato, stesso principio "chi lancia un processo reale lo
    ripulisce" gia' seguito da ogni test di questa sessione, qui esteso anche al profilo su
    disco, non solo al processo."""
    executable = find_edge_executable()
    user_data_dir = tempfile.mkdtemp(prefix="jake_edge_")
    process = subprocess.Popen([
        str(executable), url, f"--user-data-dir={user_data_dir}", "--new-window",
        *_EDGE_ISOLATION_ARGS,
    ])
    return IsolatedBrowserProcess(process, user_data_dir)


def find_page_document(adapter: UIAutomationAdapter, browser_window, timeout_seconds: float = 10.0):
    """F3.6.1/F3.6.2: la radice `Document` della pagina caricata - l'UNICO confine STRUTTURALE tra
    il chrome del browser (barra degli indirizzi, tab, impostazioni) e il contenuto web vero, non
    richiede indovinare quali nodi sono "chrome" con un elenco di nomi fragile. Questa stessa
    chiamata "sveglia" l'albero di accessibilita' di Chromium se non lo e' gia' (vedi il docstring
    del modulo) - un timeout piu' lungo del default di `SelectorEngine` (5s) perche' la prima
    sveglia puo' richiedere piu' tempo della sola ricerca di un elemento gia' attivo."""
    engine = SelectorEngine(adapter)
    return engine.wait_for_unique_element(
        browser_window, ElementSelector(control_type="Document"), timeout_seconds=timeout_seconds,
    )
