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

**Terzo buco reale, trovato leggendo davvero la barra degli indirizzi, non ipotizzato**: il testo
mostrato NON e' sempre l'URL esatto navigato - per un `file:///` locale, Edge lo mostra
normalizzato (percorso Windows con `/`, senza lo schema `file:///` davanti), verificato
confrontando il valore letto con l'URL passato a `launch_isolated_browser` (diversi carattere per
carattere). `read_address_bar_text` restituisce quindi un TESTO VISUALIZZATO, non un URL
garantito identico a quello navigato - un chiamante che deve VERIFICARE la navigazione (F3.6.5)
deve confrontare per SOTTOSTRINGA/normalizzazione, mai per uguaglianza esatta stringa-a-stringa.
Non verificato per `http(s)://` (richiederebbe navigare verso un sito reale, fuori dallo scopo
"solo fixture locale" di questo incremento - dichiarato onesto "non provato", non esteso per
analogia).

**Quarto buco (in realta' una RASSICURAZIONE reale, trovata non assunta) - F3.6.7, "redigere
password e campi sensibili"**: un campo `<input type="password">` con un valore VERO
("segreto123", non vuoto - un valore vuoto non avrebbe provato nulla) espone
`CurrentIsPassword=True` via UI Automation (`False` per un campo di testo normale, verificato il
contrasto) - un segnale STRUTTURALE, non un'euristica sul nome del campo. Piu' importante: il
pattern Value di UN CAMPO PASSWORD restituisce gia' caratteri SOSTITUTIVI mascherati
(`CurrentValue` NON e' mai il testo vero "segreto123"), Chromium lo protegge GIA' da solo a
livello di UI Automation, prima che questo modulo debba fare qualunque cosa - verificato leggendo
il valore per davvero, non assunto dalla documentazione. `is_password_field()` espone il segnale
strutturale per un chiamante che debba SAPERE se un campo e' sensibile PRIMA di interagirci (es.
per richiedere una policy, F3.6.6/F3.4.3, non ancora collegata) - non una funzione di redazione,
che non serve per il pattern Value (gia' mascherato dal browser).

**F3.6.7 (resto - OCR/clipboard, CHIUSO in un incremento successivo, 20/09/2026 - vedi
`tests/test_browser_adapter.py::RealBrowserFixtureTests.
test_ocr_of_a_real_screenshot_never_exposes_the_password_value`/
`test_copying_from_a_real_password_field_never_reaches_the_clipboard`)**: entrambi i percorsi
dichiarati "non verificati" restano RASSICURAZIONI reali, non buchi - verificato con uno
screenshot vero e un click+Ctrl+A+Ctrl+C reali, non assunto. OCR: l'API OCR di Windows non legge
alcun testo dai puntini mascherati del campo password (uno screenshot INTERO dello schermo,
letto per davvero, non mostra mai "segreto123" ne' alcun testo al loro posto - un controllo
positivo su un campo NORMALE prova che l'OCR funzionava davvero, non falliva in silenzio, e un
ritaglio piccolo si e' rivelato un LIMITE REALE dell'API OCR stessa, non affidabile sotto una
certa dimensione - trovato investigando, corretto usando lo screenshot intero). Clipboard:
Chromium BLOCCA interamente la copia da un campo password (Ctrl+C non cambia affatto la
clipboard, verificato contro un controllo positivo sullo stesso meccanismo su un campo normale,
che copia correttamente) - non solo maschera il valore copiato, impedisce la copia stessa.

**Quinta osservazione - F3.6.3, "supportare navigazione" (prima fetta, nessun codice nuovo
necessario)**: la composizione GIA' esistente di `click_element` (F3.4.2, Invoke su un
`Hyperlink`) + `read_address_bar_text` (F3.6.5) + `find_page_document` (F3.6.1) basta gia' a
completare e verificare una navigazione VERA (non ipotizzata - vedi
`tests/test_browser_adapter.py::test_clicking_a_real_link_navigates_to_a_second_local_page`, che
clicca un link verso una seconda pagina locale reale, non un'ancora "#" sulla stessa pagina, e
verifica sia il cambio di URL sia il nuovo `Document` caricato). Form/tab/download
restano invece NON affrontati (vedi sotto) - la navigazione tramite un link e' il caso piu'
semplice tra quelli dichiarati da F3.6.3, non l'intero sotto-punto. **"Upload" (resto di F3.6.3)
e' stato CHIUSO in un incremento successivo** (F3.1.2 Task 13, adozione - vedi il docstring di
`UIAutomationAdapter.snapshot_win32_top_level_window_handles`/`element_from_handle`): il dialogo
NATIVO "Apri" di Windows, prima non raggiungibile da UI Automation (buco documentato dopo
un'indagine, mai risolto), e' ora guidabile dall'inizio alla fine (digitare il percorso, cliccare
"Apri") tramite quella nuova coppia di metodi - verificato con un upload reale in
`tests/test_browser_adapter.py::test_uploading_a_real_local_file_through_the_native_open_dialog`.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- F3.6.2 (resto): un vocabolario/euristica per "istruzioni dell'utente" dentro la pagina (oggi
  distingue solo chrome del browser da contenuto pagina, non contenuto pagina genuino da testo
  che TENTA di sembrare un'istruzione per l'agente);
- F3.6.3 (resto - form/tab/download con policy specifica, e navigazione diretta per URL invece
  che tramite un link esistente sulla pagina - "upload" e' CHIUSO, vedi sopra);
- F3.6.4 (CHIUSO in un incremento successivo - `skills/read_web_page.py::ReadWebPageSkill`, la
  prima skill reale a leggere testo di pagina, collegata a
  `core/taint.py::EXTERNAL_CONTENT_INTENTS`);
- F3.6.5 (resto - "stato controllo"/"risposta del sito": nessun codice di stato HTTP o segnale di
  caricamento ancora letto, solo il testo della barra degli indirizzi. Un vero stato HTTP
  richiederebbe Chrome DevTools Protocol, escluso per decisione esplicita con l'utente - resta
  dichiarato fuori scope, non un'omissione);
- F3.6.6 (rispettare CAPTCHA/login/protezioni anti-automazione - `launch_isolated_browser` non
  tenta mai login automatico, ma non c'e' ancora una policy esplicita che lo vieti - ne'
  `is_password_field()` e' ancora collegata a nessuna decisione di policy);
- trovare l'eseguibile del browser SOLO su Edge, un percorso fisso (`_CANDIDATE_EDGE_PATHS`) -
  Chrome/Firefox non ancora supportati, ne' un rilevamento piu' robusto del browser predefinito
  dell'utente."""
import subprocess
import tempfile
from pathlib import Path

import comtypes
import comtypes.client

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402 (deve seguire GetModule)

from core.computer_use.selector import ElementSelector, SelectorEngine  # noqa: E402
from core.computer_use.ui_automation_adapter import UIAutomationAdapter  # noqa: E402

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


def read_page_text(adapter: UIAutomationAdapter, document, max_depth: int = 20) -> str:
    """F3.6.1 (resto - leggere il testo visibile della pagina, non solo trovare un elemento per
    nome): cammina l'albero sotto `document` (F3.2, `describe_tree`) e raccoglie il NOME di ogni
    nodo non vuoto, in ordine - lo stesso genere di estrazione gia' fatta da `core/vision/
    screen.py::read_screen_text` per l'OCR, qui dal DOM reale invece che da pixel.

    Nessuna esclusione esplicita per i campi password (F3.6.7): `ElementInfo`/`describe_tree`
    (F3.2.3) non espongono MAI il pattern Value di un elemento, solo `name`/`automation_id`/
    `control_type`/`enabled`/`selected`/`toggle_state`/`focused` - il NOME di un campo password e'
    la sua ETICHETTA (es. "Password"), mai il suo valore (quello vive SOLO nel pattern Value, letto
    solo da `read_address_bar_text`/uno strumento dedicato, mai da questa funzione) - verificato
    con la fixture reale (il campo password compare come `Edit 'Password'`, non con "segreto123"),
    non assunto dalla semantica HTML/ARIA.

    F3.6.4 ("isolare testo web come non fidato") NON e' affrontato qui: nessuna skill/intent
    ancora consuma questo testo, quindi non c'e' ancora un intent REALE da passare a
    `core/taint.py::wrap_external_content` (che richiede un intent gia' censito nella tassonomia
    esistente, non uno inventato per l'occasione) - un futuro collegamento resta un incremento di
    adozione a se', lo stesso principio gia' seguito da F1.5.1 per introdurre un pezzo alla
    volta."""
    tree = adapter.describe_tree(document, max_depth=max_depth)
    if tree is None:
        return ""
    pieces: list[str] = []

    def _walk(node) -> None:
        if node.name:
            pieces.append(node.name)
        for child in node.children:
            _walk(child)

    _walk(tree)
    return "\n".join(pieces)


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


def read_address_bar_text(adapter: UIAutomationAdapter, browser_window, timeout_seconds: float = 5.0) -> str | None:
    """F3.6.5 (prima fetta - "verificare URL"): il testo MOSTRATO nella barra degli indirizzi,
    letto dal pattern Value - vedi il docstring del modulo per il buco reale gia' trovato (NON e'
    garantito identico all'URL navigato, es. un `file:///` locale viene normalizzato). `None`
    onesto se il pattern Value non e' disponibile (mai un valore indovinato), stesso principio
    gia' seguito ovunque in questo progetto.

    **Fix di un fallimento reale in CI, non ipotizzato**: la prima versione cercava l'elemento
    per NOME localizzato in italiano ("Indirizzo e barra di ricerca") - funzionava in locale (Edge
    in italiano) ma falliva SEMPRE sul runner CI (Edge in inglese, un nome diverso). Corretto
    cercando SOLO per `control_type` (mai per nome, quindi indipendente dalla lingua), ma
    ristretto al `ToolBar` del browser invece che all'intera finestra - un `Edit` cercato
    sull'intera finestra sarebbe ambiguo quando la pagina contiene un proprio campo di testo (es.
    questa stessa fixture), dato che l'albero della pagina e quello del chrome del browser sono
    entrambi discendenti della stessa finestra di primo livello."""
    engine = SelectorEngine(adapter)
    toolbar = engine.wait_for_unique_element(
        browser_window, ElementSelector(control_type="ToolBar"), timeout_seconds=timeout_seconds,
    )
    address_bar = engine.wait_for_unique_element(
        toolbar, ElementSelector(control_type="Edit"), timeout_seconds=timeout_seconds,
    )
    try:
        pattern = address_bar.GetCurrentPattern(UIA.UIA_ValuePatternId)
    except (ValueError, comtypes.COMError):
        pattern = None
    if not pattern:
        return None
    value_pattern = pattern.QueryInterface(UIA.IUIAutomationValuePattern)
    return value_pattern.CurrentValue


def is_password_field(element) -> bool:
    """F3.6.7 (prima fetta - "redigere password e campi sensibili"): vero se l'elemento e' un
    campo password (`<input type="password">`, verificato via `CurrentIsPassword` - un segnale
    STRUTTURALE letto da Chromium/UI Automation, non un'euristica sul NOME del campo, che
    potrebbe mancare o mentire). `False` onesto (mai un'eccezione) se la proprieta' non e'
    leggibile - lo stesso principio "onesto ma non fragile" gia' seguito da `describe_element`
    (F3.2): un elemento su cui questa proprieta' non e' disponibile non e' TRATTATO come
    password, ma nemmeno fa fallire la chiamata.

    Non una funzione di redazione - vedi il docstring del modulo: il pattern Value di un vero
    campo password restituisce GIA' caratteri mascherati (verificato, non assunto), Chromium lo
    protegge da solo prima che questo modulo debba fare qualunque cosa. Questa funzione serve a
    un chiamante che debba SAPERE se un campo e' sensibile PRIMA di interagirci (es. per
    richiedere una policy, F3.6.6, non ancora collegata)."""
    try:
        return bool(element.CurrentIsPassword)
    except (ValueError, comtypes.COMError):
        return False
