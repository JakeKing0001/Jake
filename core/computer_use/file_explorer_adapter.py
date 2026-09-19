"""File Explorer adapter (F3.7.1, prima fetta di F3.7 - "Adapter applicativi", primo
nell'ordine dichiarato "Esplora file -> Impostazioni -> browser -> VS Code -> terminale -> Office
-> media -> messaggistica" - mai iniziata prima d'ora, vedi ROADMAP_EXECUTION.md sezione F3.7,
dipende da F3.4). Apre/localizza/chiude una finestra di Esplora File REALE tramite UI Automation
(F3.2) - lo stesso principio "riusa l'infrastruttura gia' costruita" gia' seguito per il browser
(F3.6), nessuna nuova libreria.

**Rischio di sicurezza REALE, verificato PRIMA di scrivere qualunque test, non ipotizzato**:
`explorer.exe` e' anche il processo SHELL di Windows (gestisce desktop/taskbar) - un SOLO
processo esiste normalmente per questo (verificato con `Get-Process explorer` su questa macchina
PRIMA di aprire qualunque finestra: un solo PID). Terminare quel processo per errore chiuderebbe
l'INTERO desktop dell'utente, non solo una finestra - una conseguenza inaccettabile per un bug in
un test. Verificato invece (non assunto) che aprire una NUOVA finestra di Esplora File su un
percorso specifico (`explorer.exe <percorso>`) crea un processo `explorer.exe` SEPARATO e
DISTINTO da quello del guscio (verificato: un secondo PID e' comparso, il primo e' rimasto
invariato dopo aver chiuso il secondo). Terminare QUELLO specifico PID - mai per NOME processo
"explorer" (che colpirebbe anche il guscio), sempre per il PID ESATTO trovato via UI Automation
dopo aver localizzato la finestra - e' quindi sicuro.

**Secondo buco reale, coerente con quello gia' trovato per il browser (F3.6.5)**:
`subprocess.Popen(["explorer.exe", path]).pid` NON corrisponde al PID reale che possiede la
finestra (verificato: numeri diversi) - lo stesso genere di indirezione gia' trovato per la
fixture Qt (F3.2, un launcher della venv) e diverso da Edge (dove i due PID coincidono, F3.6.1).
`close_explorer_window()` non accetta mai un PID passato dal chiamante per questo motivo - lo
TROVA da solo tramite `UIAutomationAdapter.find_window_by_title_containing` (F3.7, adozione: il
titolo completo include un suffisso dipendente dalla lingua del sistema, "- Esplora file"/
"- File Explorer", che un'uguaglianza esatta non potrebbe mai prevedere), l'unico modo verificato
per essere sicuri di quale finestra si sta chiudendo prima di terminarne il processo.

**Terzo buco reale, un rischio di PRIVACY concreto - non teorico, analogo a quello gia' trovato
per il browser (F3.6.2)**: l'intera finestra di Esplora File contiene anche il riquadro di
NAVIGAZIONE a sinistra (`Tree`), che espone i nomi VERI delle scorciatoie personali dell'utente
(account OneDrive, cartelle recenti, dispositivi di rete...) - verificato camminando l'albero
completo durante l'indagine, mai salvato ne' mostrato oltre la finestra di debug locale di quella
sessione. `list_files()` legge quindi SOLO la lista file (`List`, il controllo che mostra
`documento1.txt` eccetera), mai l'intera finestra - lo stesso principio "isolare il contenuto
rilevante dal chrome circostante" gia' seguito per `find_page_document` (F3.6.1/F3.6.2).

**Quarto buco reale, la stessa famiglia del secondo**: la finestra contiene DUE controlli
`List` (verificato, non assunto) - quella dei file (senza `automation_id`, nome localizzato
"Visualizzazione elementi"/"Items view") E quella della barra delle SCHEDE
(`automation_id="TabListView"`, STABILE e indipendente dalla lingua - un dettaglio interno della
shell di Windows, non un'etichetta tradotta). `list_files()` esclude quella con
`automation_id="TabListView"` invece di cercare per nome (che fallirebbe in un Windows non
italiano, lo stesso genere di buco gia' corretto per la barra degli indirizzi del browser,
F3.6.5) - un'esclusione STRUTTURALE, non un'euristica sul testo.

**Sesto buco reale, trovato in CI dopo la pubblicazione, non ipotizzato**: i NOMI restituiti da
`list_files()` possono includere o NON includere l'estensione del file (es. "documento1.txt"
oppure solo "documento1"), a seconda di un'impostazione di sistema di Esplora File ("Nascondi le
estensioni per i tipi di file conosciuti") - VERA di default su un'installazione Windows pulita
(verificato: il runner CI mostrava i nomi SENZA estensione), FALSA su altre macchine (dove le
estensioni sono visibili) - entrambi i comportamenti osservati per davvero, non un'assunzione su
quale sia "normale". `list_files()` non normalizza questo - legge onestamente cio' che Esplora
File mostra DAVVERO in quel momento su quella macchina, un chiamante che ha bisogno del nome file
COMPLETO E AFFIDABILE (con estensione garantita) dovrebbe usare l'API del filesystem
(`pathlib`/`os`), non questo adapter - la stessa distinzione gia' vista altrove in questo
progetto tra "cio' che l'interfaccia MOSTRA" e "cio' che il sistema SA per certo"."""
import subprocess
import time
from pathlib import Path

from core.computer_use.ui_automation_adapter import UIAutomationAdapter


def open_explorer_window(path: Path) -> None:
    """Apre una finestra di Esplora File sul percorso dato. Non restituisce un oggetto processo
    utilizzabile per la terminazione (vedi il docstring del modulo: il PID del `Popen` non e'
    quello reale della finestra) - il chiamante deve invece localizzare e chiudere la finestra
    tramite `close_explorer_window()`, che la trova per titolo, non per PID indovinato.

    Attende che il PROCESSO LANCIATO (non la finestra reale, un launcher a se') esca da solo -
    esce quasi subito dopo aver passato la richiesta al guscio, verificato non assunto - solo per
    evitare un `ResourceWarning` di Python su un `Popen` mai raccolto, non per sincronizzarsi con
    la finestra vera (per quello serve `find_explorer_window`, un `Popen` uscito non significa
    affatto che la finestra sia gia' visibile)."""
    launcher = subprocess.Popen(["explorer.exe", str(path)])
    try:
        launcher.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def find_explorer_window(adapter: UIAutomationAdapter, folder_name: str, timeout_seconds: float = 10.0):
    """Trova la finestra di Esplora File aperta su una cartella chiamata `folder_name` - cerca
    per SOTTOSTRINGA (`find_window_by_title_containing`, F3.7), mai per uguaglianza esatta: vedi
    il docstring del modulo per il perche'. `folder_name` deve essere un nome scelto dal
    chiamante, abbastanza specifico da non corrispondere ad altre finestre gia' aperte
    dall'utente - un'ambiguita' solleva `AmbiguousWindowError` invece di scegliere a caso."""
    return adapter.find_window_by_title_containing(folder_name, timeout_seconds=timeout_seconds)


class ExplorerWindowStillRunningError(Exception):
    """`taskkill` e' tornato ma il processo risulta ancora vivo oltre il timeout dato - onesto
    invece di dare per scontato che il comando abbia avuto l'effetto voluto solo perche' non ha
    sollevato (lo stesso principio "verificare l'effetto, non fidarsi della chiamata" gia' seguito
    ovunque in questo progetto - es. `core/execution_safety.py::verify_effect`)."""


def close_explorer_window(adapter: UIAutomationAdapter, folder_name: str, timeout_seconds: float = 10.0) -> int:
    """Chiude SOLO la finestra di Esplora File trovata per `folder_name` - mai per nome processo
    "explorer.exe" (vedi il docstring del modulo per il rischio reale di colpire il guscio di
    Windows). Il PID da terminare viene letto DALLA FINESTRA stessa (`CurrentProcessId`), mai
    passato o indovinato dal chiamante.

    ATTENDE che il processo sia DAVVERO terminato prima di restituirsi (fino a `timeout_seconds`,
    poi solleva `ExplorerWindowStillRunningError`) - `taskkill` che ritorna non e' prova che il
    processo sia gia' sparito nell'istante esatto in cui il comando finisce, lo stesso principio
    "verificare, non assumere" gia' seguito ovunque in questo modulo. Restituisce il PID chiuso,
    utile a un chiamante (es. un test) che voglia una propria verifica indipendente."""
    window = find_explorer_window(adapter, folder_name, timeout_seconds=timeout_seconds)
    pid = window.CurrentProcessId
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)

    deadline = time.monotonic() + timeout_seconds
    while _process_exists(pid):
        if time.monotonic() >= deadline:
            raise ExplorerWindowStillRunningError(f"il processo {pid} risulta ancora vivo dopo {timeout_seconds}s")
        time.sleep(0.1)
    return pid


def _process_exists(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False,
    )
    return str(pid) in result.stdout


class FileListNotFoundError(Exception):
    """Nessun controllo Lista file (distinto dalla lista delle schede) trovato nella finestra -
    vedi il docstring del modulo per come le due liste vengono distinte."""


def list_files(adapter: UIAutomationAdapter, window) -> list[str]:
    """F3.7.1 (prima capacita' - "leggere il contenuto di una cartella"): i nomi dei file/cartelle
    mostrati nella lista file della finestra - SOLO quella lista (vedi il docstring del modulo per
    il rischio di privacy del resto della finestra, mai camminata per intero qui), distinta dalla
    lista delle schede tramite `automation_id` (segnale STRUTTURALE, non il nome localizzato)."""
    candidates = adapter.find_matching_elements(window, control_type="List")
    file_lists = [c for c in candidates if c.CurrentAutomationId != "TabListView"]
    if len(file_lists) != 1:
        raise FileListNotFoundError(
            f"attesa esattamente una lista file (esclusa la barra delle schede), trovate {len(file_lists)}"
        )
    tree = adapter.describe_tree(file_lists[0], max_depth=2)
    if tree is None:
        return []
    return [child.name for child in tree.children if child.control_type == "ListItem"]
