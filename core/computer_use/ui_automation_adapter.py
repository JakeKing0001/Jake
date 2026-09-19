"""Windows UI Automation adapter (F3.2.1-F3.2.3, prima fetta di F3.2 - "Computer Use Engine 3.0",
mai iniziata prima d'ora - vedi ROADMAP_EXECUTION.md sezione F3.2). Sostituisce, per le app che lo
supportano, il controllo a pixel/OCR di `core/computer_agent.py` (l'attuale `ComputerAgent`, che
resta invariato qui - il collegamento tra i due e' l'executor semantico/scala di ripiego di
F3.4/F3.5, non affrontato in questo incremento) con l'albero SEMANTICO reale che Windows espone
per ogni finestra: nomi, ruoli, stato abilitato/selezionato/con focus, invece di indovinare da
uno screenshot.

`comtypes` (gia' una dipendenza del progetto - `requirements/hud.txt`, usata da `pycaw` per
CoreAudio - nessun nuovo pacchetto) invece di un helper C++/`pywinauto`: F3.2.7 dichiara
esplicitamente "valutare COM diretto vs helper C++ con benchmark" come passo SUCCESSIVO, non un
prerequisito - COM diretto e' il punto di partenza piu' semplice, stesso principio "il piu'
semplice che funziona" gia' seguito altrove nel progetto. `comtypes.client.GetModule` genera i
binding Python per `UIAutomationCore.dll` dal type library registrato in Windows (verificato
funzionante su questa macchina, non assunto).

Verificato empiricamente PRIMA di scrivere questo modulo (non ipotizzato) lanciando la fixture di
F3.1.1 (`benchmarks/computer_use_fixture.py`) e interrogandola con UI Automation vera: gli
`accessibleName` impostati sui widget Qt (F3.1.1) arrivano davvero come Name qui - "Aggiungi",
"Campo di testo", "Struttura ad albero", perfino "Categoria A" come nodo TreeItem - confermando
che la scelta di PySide6 per la fixture (dichiarata in F3.1.1) espone davvero un albero
utilizzabile, non solo in teoria.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di tutta questa sessione):
- F3.2.2 (meta' - la cache): ogni proprieta' letta qui e' una chiamata COM dal vivo
  (`element.CurrentName` eccetera), non una `IUIAutomationCacheRequest` - corretto ma non
  ottimizzato, un problema di prestazioni dichiarato, non di correttezza;
- F3.2.4 (subscription agli eventi UIA per invalidare cache - non c'e' ancora una cache da
  invalidare, vedi sopra);
- F3.2.5 (finestre elevate/provider mancanti - oggi un provider mancante fa fallire la singola
  proprieta' con un errore COM non gestito esplicitamente per ogni caso, non con un fallback
  dedicato);
- F3.2.6 (limitare lo scope alla finestra target per prestazioni/privacy - `find_window_by_title`
  gia' cerca solo tra i figli diretti del desktop, non l'intero schermo, ma nessun limite
  ulteriore e' ancora applicato);
- F3.2.7 (benchmark COM diretto vs helper C++);
- il supporto a cinque app REALI (il criterio di uscita completo di F3.2) - qui solo la fixture di
  F3.1.1 e' verificata."""
import time
from dataclasses import dataclass, field, replace

import comtypes
import comtypes.client

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402 (deve seguire GetModule)

# F3.2.3 ("esporre role... e patterns"): un nome leggibile invece del solo intero opaco
# UIA_ControlTypeId - calcolato una volta dal modulo generato da comtypes (non un elenco scritto
# a mano che potrebbe disallinearsi dalle costanti vere), es. UIA_ButtonControlTypeId -> "Button".
_CONTROL_TYPE_NAMES: dict[int, str] = {
    getattr(UIA, _name): _name[len("UIA_"):-len("ControlTypeId")]
    for _name in dir(UIA)
    if _name.startswith("UIA_") and _name.endswith("ControlTypeId")
}
# L'inverso della mappa sopra (F3.3.1, usata da core/computer_use/selector.py per costruire una
# condizione UIA nativa da un nome leggibile come "Button" invece dell'intero opaco).
_CONTROL_TYPE_IDS: dict[str, int] = {_name: _id for _id, _name in _CONTROL_TYPE_NAMES.items()}


def control_type_id_for(control_type_name: str) -> int:
    """L'inverso di `ElementInfo.control_type` - solleva `ValueError` per un nome sconosciuto
    invece di restituire un intero indovinato che non corrisponde a nessun vero UIA_ControlTypeId
    (una condizione di ricerca con un intero a caso non solleverebbe un errore COM, semplicemente
    non troverebbe mai nulla - un fallimento silenzioso peggiore di un errore esplicito subito)."""
    try:
        return _CONTROL_TYPE_IDS[control_type_name]
    except KeyError:
        raise ValueError(f"control_type sconosciuto: {control_type_name!r}") from None


class WindowNotFoundError(Exception):
    """Nessuna finestra visibile con il titolo cercato entro il timeout dato - onesto (F3.2.5
    dichiara "provider mancanti" come passo successivo, ma "la finestra non esiste ancora/piu'"
    e' un caso diverso, gia' distinto qui con la propria eccezione invece di restituire None e
    lasciare al chiamante indovinare il perche'.)"""


class AmbiguousWindowError(Exception):
    """F3.7 (adozione, motivata da Esplora File - vedi `find_window_by_title_containing`): piu'
    di una finestra di primo livello contiene la sottostringa cercata - stesso principio "rifiuta
    l'ambiguita' invece di sceglierne una a caso" gia' seguito da `SelectorEngine.find_unique`
    (F3.3.3), qui a livello di finestra: scegliere una finestra SBAGLIATA tra piu' candidate
    sarebbe particolarmente pericoloso per un'app come Esplora File, dove un'azione (es. chiudere
    la finestra) potrebbe finire sulla finestra dell'utente invece che su quella creata da Jake."""


@dataclass(frozen=True)
class ElementInfo:
    """F3.2.3: esattamente le proprieta' dichiarate dalla roadmap - "role, name, automation id,
    bounds, enabled, selected, focused e patterns" (i pattern, oggi solo SelectionItem per
    `selected`, sono impliciti nel VALORE di questo campo: None quando l'elemento non supporta
    affatto il pattern Selection, non un `False` inventato - stesso principio "onesto None, mai
    un valore indovinato" gia' seguito ovunque in questo progetto, es. `core/action_snapshot.py`).
    `bounds` e' (left, top, width, height) in pixel schermo, non (left, top, right, bottom) -
    la forma piu' immediatamente utile per un futuro click (F3.4), che ha bisogno del CENTRO
    dell'elemento, calcolabile da width/height senza dover prima sottrarre le coordinate."""

    name: str
    automation_id: str
    control_type: str
    bounds: tuple[int, int, int, int]
    enabled: bool
    selected: bool | None
    # F3.4 (adozione - trovato verificando core/computer_use/executor.py::ActionExecutor.toggle,
    # non pianificato in anticipo): "on"/"off"/"indeterminate", None per un elemento che non
    # supporta affatto il pattern Toggle (stesso principio "onesto None" di `selected` sopra) -
    # senza questo campo non c'era modo di VERIFICARE che Toggle() avesse davvero cambiato lo
    # stato di una casella di spunta, solo che la chiamata non avesse sollevato un errore.
    toggle_state: str | None
    focused: bool
    children: tuple["ElementInfo", ...] = field(default_factory=tuple)


class UIAutomationAdapter:
    """F3.2.1: "creare UIAutomationAdapter dietro interfaccia, senza legarlo alle skill" - nessuna
    skill importa `comtypes`/`UIAutomationClient` direttamente, solo questo modulo. Ogni istanza
    inizializza COM sul thread chiamante (`comtypes.CoInitialize()`, richiesto da UI Automation,
    un sottosistema COM) - un'istanza NON e' quindi condivisibile tra thread diversi senza una
    propria inizializzazione COM per ciascuno, stesso limite gia' noto per altri client COM nel
    progetto (es. `pycaw`)."""

    def __init__(self) -> None:
        comtypes.CoInitialize()
        self._uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)

    def find_window_by_title(self, title: str, timeout_seconds: float = 5.0):
        """Cerca tra i figli DIRETTI del desktop (non l'intero schermo con
        `TreeScope_Descendants`, che attraverserebbe anche l'interno di ogni finestra solo per
        trovarne una per nome) - le finestre di primo livello sono gia' tutte figlie dirette del
        desktop. Ritenta con un breve intervallo fino al timeout: una finestra appena lanciata
        (es. `benchmarks/computer_use_fixture.py` in un processo separato) puo' non essere ancora
        visibile a UI Automation nell'istante esatto in cui questo metodo viene chiamato - lo
        stesso genere di corsa gia' gestito altrove nel progetto con un timeout esplicito invece
        di un singolo tentativo ottimistico.

        **Buco reale trovato scrivendo il test negativo di questo stesso metodo, non ipotizzato**:
        `FindFirst` senza corrispondenza NON restituisce Python `None` - restituisce un vero
        `POINTER(IUIAutomationElement)` con puntatore nullo (`ptr=0x0`), un oggetto Python DIVERSO
        da `None` ma FALSY (`bool(x) is False`). Un controllo `is not None` lo tratterebbe quindi
        come "trovato" al primo tentativo, restituendo un elemento fantasma le cui proprieta'
        sollevano `ValueError: NULL COM pointer access` non appena lette - verificato riproducendo
        l'errore con una ricerca per un titolo che non esiste, non assunto dalla documentazione
        COM. Il controllo giusto e' sulla VERITA' dell'oggetto (`if window:`), non sulla sua
        identita' con `None` - stesso genere di insidia gia' incontrata con altri puntatori COM
        null in questo stesso modulo (vedi `describe_tree` per l'identico buco nel cammino
        dell'albero)."""
        condition = self._uia.CreatePropertyCondition(UIA.UIA_NamePropertyId, title)
        return self._find_top_level_window(condition, f"con titolo {title!r}", timeout_seconds)

    def find_window_by_process_id(self, process_id: int, timeout_seconds: float = 5.0):
        """F3.6 (adozione, motivata da un browser - vedi `core/computer_use/browser_adapter.py`):
        trova la finestra di primo livello di un PROCESSO noto (es. `subprocess.Popen(...).pid`)
        invece che per titolo - necessario quando il titolo della finestra e' imprevedibile in
        anticipo (il titolo di un browser cambia con ogni pagina caricata, il nome della scheda
        attiva, il profilo). Stessa identica logica di `find_window_by_title` (fattorizzata in
        `_find_top_level_window`, condivisa da entrambi), solo il criterio di corrispondenza
        cambia."""
        condition = self._uia.CreatePropertyCondition(UIA.UIA_ProcessIdPropertyId, process_id)
        return self._find_top_level_window(condition, f"del processo {process_id}", timeout_seconds)

    def _find_top_level_window(self, condition, description: str, timeout_seconds: float):
        deadline = time.monotonic() + timeout_seconds
        while True:
            root = self._uia.GetRootElement()
            window = root.FindFirst(UIA.TreeScope_Children, condition)
            if window:
                return window
            if time.monotonic() >= deadline:
                raise WindowNotFoundError(f"nessuna finestra visibile {description} entro {timeout_seconds}s")
            time.sleep(0.1)

    def find_window_by_title_containing(self, substring: str, timeout_seconds: float = 5.0):
        """F3.7 (adozione, motivata da Esplora File): trova una finestra di primo livello il cui
        titolo CONTIENE `substring` (non uguaglianza esatta come `find_window_by_title`) -
        necessario quando il titolo completo include un suffisso dipendente dalla LINGUA del
        sistema (es. "cartella - Esplora file" in italiano, "folder - File Explorer" in inglese -
        lo stesso genere di buco gia' trovato per la barra degli indirizzi del browser, F3.6.5,
        qui evitato dall'inizio invece di corretto dopo un fallimento in CI) - un'uguaglianza
        esatta non potrebbe mai prevedere il suffisso giusto. `substring` deve restare la parte
        CONTROLLATA dal chiamante (es. il nome di una cartella scelta da Jake), mai il suffisso
        dipendente dalla lingua.

        Solleva `AmbiguousWindowError` (non una scelta arbitraria) se PIU' di una finestra
        corrisponde - a differenza di `_find_top_level_window` (usato da `find_window_by_title`/
        `find_window_by_process_id`, dove una corrispondenza ESATTA per nome o PID e' gia'
        intrinsecamente univoca), qui una sottostringa generica potrebbe corrispondere a piu'
        finestre per costruzione, quindi l'ambiguita' e' un caso reale da gestire, non solo
        teorico."""
        deadline = time.monotonic() + timeout_seconds
        true_condition = self._uia.CreateTrueCondition()
        while True:
            root = self._uia.GetRootElement()
            candidates = root.FindAll(UIA.TreeScope_Children, true_condition)
            matches = []
            for i in range(candidates.Length):
                candidate = candidates.GetElement(i)
                try:
                    name = candidate.CurrentName
                except (ValueError, comtypes.COMError):
                    continue
                if name and substring in name:
                    matches.append(candidate)
            if len(matches) > 1:
                raise AmbiguousWindowError(
                    f"{len(matches)} finestre contengono {substring!r}: servono criteri piu' precisi"
                )
            if len(matches) == 1:
                return matches[0]
            if time.monotonic() >= deadline:
                raise WindowNotFoundError(f"nessuna finestra visibile contenente {substring!r} entro {timeout_seconds}s")
            time.sleep(0.1)

    def describe_element(self, element) -> ElementInfo | None:
        """Solo l'elemento dato, senza figli (`children` resta vuoto) - vedi `describe_tree` per
        una copia ricorsiva. Proprieta' lette dal vivo (`Current*`), non da una cache (F3.2.2,
        passo successivo dichiarato).

        `None` (non un `ElementInfo` con campi a caso) quando l'elemento non e' davvero
        leggibile: **buco reale trovato camminando l'albero della fixture stessa, non ipotizzato**
        - elementi di CHROME nativo della finestra (System Menu/icone della barra del titolo,
        "Sistema" nel dump italiano) hanno provider UI Automation incompleti nel mondo reale,
        verificato riproducendo l'errore (`ValueError: NULL COM pointer access` leggendo
        `CurrentBoundingRectangle` su un elemento restituito apparentemente valido da
        `GetNextSiblingElement`). Un `ValueError`/`comtypes.COMError` durante la lettura delle
        proprieta' di base significa "questo elemento non e' realmente ispezionabile", non "ha
        proprieta' vuote" - onesto `None` invece di inventare un `ElementInfo`."""
        try:
            bounds = element.CurrentBoundingRectangle
            name = element.CurrentName or ""
            automation_id = element.CurrentAutomationId or ""
            control_type = element.CurrentControlType
            enabled = bool(element.CurrentIsEnabled)
            focused = bool(element.CurrentHasKeyboardFocus)
        except (ValueError, comtypes.COMError):
            return None
        return ElementInfo(
            name=name, automation_id=automation_id,
            control_type=_CONTROL_TYPE_NAMES.get(control_type, str(control_type)),
            bounds=(bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top),
            enabled=enabled, selected=self._selection_state_of(element),
            toggle_state=self._toggle_state_of(element), focused=focused,
        )

    def _toggle_state_of(self, element) -> str | None:
        """None (non una stringa a caso) per un elemento che non supporta affatto il pattern
        Toggle - stesso principio di `_selection_state_of` sotto. "on"/"off"/"indeterminate"
        (mai l'intero opaco `UIA_ToggleState_*`) per coerenza con `control_type`, gia' una
        stringa leggibile invece di un intero."""
        try:
            pattern = element.GetCurrentPattern(UIA.UIA_TogglePatternId)
            if not pattern:
                return None
            toggle = pattern.QueryInterface(UIA.IUIAutomationTogglePattern)
            state = toggle.CurrentToggleState
        except (ValueError, comtypes.COMError):
            return None
        return {
            UIA.ToggleState_On: "on", UIA.ToggleState_Off: "off", UIA.ToggleState_Indeterminate: "indeterminate",
        }.get(state, str(state))

    def _selection_state_of(self, element) -> bool | None:
        """None (non False) per un elemento che non supporta affatto il pattern SelectionItem
        (es. un bottone) - un bottone "non selezionato" e un bottone per cui la domanda "e'
        selezionato?" non ha senso sono fatti DIVERSI, e confonderli inventerebbe un'informazione
        che UI Automation non fornisce. Stesso `try`/`except` di `describe_element` - lo stesso
        genere di elemento con provider incompleto puo' fallire anche qui, non solo sulle
        proprieta' di base."""
        try:
            pattern = element.GetCurrentPattern(UIA.UIA_SelectionItemPatternId)
            if not pattern:
                return None
            selection_item = pattern.QueryInterface(UIA.IUIAutomationSelectionItemPattern)
            return bool(selection_item.CurrentIsSelected)
        except (ValueError, comtypes.COMError):
            return None

    def describe_tree(self, element, max_depth: int = 8) -> ElementInfo | None:
        """F3.2.2 (meta' - la lettura dell'albero, non ancora la cache): copia ricorsiva vera
        dell'albero di CONTROLLO (non quello grezzo/raw, che includerebbe elementi puramente
        interni a Qt privi di significato semantico) fino a `max_depth` livelli, tramite
        `TreeWalker` (`GetFirstChildElement`/`GetNextSiblingElement`) invece di `FindAll` con
        `TreeScope_Descendants` - quest'ultimo restituirebbe un elenco PIATTO, perdendo la
        struttura genitore/figlio che "control/content tree" (F3.2.2) richiede esplicitamente.

        `None` quando l'elemento radice stesso non e' leggibile (vedi `describe_element`); un
        figlio non leggibile viene invece semplicemente OMESSO dall'albero (non propaga `None`
        all'intera chiamata - un elemento di chrome rotto non deve far sparire l'intero sotto-
        albero dei suoi fratelli validi). La catena dei fratelli si ferma silenziosamente al
        primo `GetNextSiblingElement` che fallisce, invece di propagare l'eccezione - stesso
        principio "onesto ma non fragile": meglio un albero parziale che nessun albero.

        **Stesso buco di `find_window_by_title` (vedi li' per la prova empirica), qui nel punto
        piu' insidioso possibile**: "nessun altro fratello"/"nessun figlio" da `GetNextSiblingElement`/
        `GetFirstChildElement` e' anch'esso un puntatore COM nullo NON-`None`, non `None` vero. Un
        controllo `is not None` non avrebbe MAI terminato la camminata da solo - si sarebbe
        fermato solo grazie a un effetto collaterale fortunato (`describe_element` sul figlio
        fantasma solleva `ValueError`, gia' catturato altrove, restituendo `None` e uscendo dal
        ramo) invece che per una condizione di uscita vera e dichiarata. Corretto controllando la
        VERITA' dell'oggetto (`if child:`/`while child:`), non la sua identita' con `None`."""
        info = self.describe_element(element)
        if info is None or max_depth <= 0:
            return info
        walker = self._uia.ControlViewWalker
        children: list[ElementInfo] = []
        try:
            child = walker.GetFirstChildElement(element)
        except (ValueError, comtypes.COMError):
            child = None
        while child:
            child_info = self.describe_tree(child, max_depth - 1)
            if child_info is not None:
                children.append(child_info)
            try:
                child = walker.GetNextSiblingElement(child)
            except (ValueError, comtypes.COMError):
                break
        # ElementInfo e' frozen (F3.2.3, stesso principio "immutabile una volta letto" gia'
        # seguito da core/action_snapshot.py::ActionSnapshot) - dataclasses.replace() costruisce
        # un nuovo oggetto con solo children cambiato, senza ripetere gli altri campi a mano (che
        # potrebbero disallinearsi se un campo viene aggiunto in futuro).
        return replace(info, children=tuple(children))

    def find_matching_elements(
        self, root, *, name: str | None = None, control_type: str | None = None,
        automation_id: str | None = None,
    ) -> list:
        """F3.3.1 (fondamenta): elementi COM GREZZI (non `ElementInfo` - il chiamante decide se e
        come descriverli, vedi `core/computer_use/selector.py::SelectorEngine`) tra i discendenti
        di `root` che soddisfano TUTTI i criteri dati, tramite le condizioni NATIVE di UI
        Automation (`FindAll` + `CreateAndCondition`) - una singola chiamata COM che filtra
        internamente a Windows, non una camminata Python + confronto manuale su ogni elemento
        (che per un albero grande costerebbe quanto l'intera `describe_tree`, gia' misurata a
        circa 1ms per elemento su un'app reale - vedi ROADMAP_EXECUTION.md sezione F3.2)."""
        conditions = []
        if name is not None:
            conditions.append(self._uia.CreatePropertyCondition(UIA.UIA_NamePropertyId, name))
        if control_type is not None:
            conditions.append(self._uia.CreatePropertyCondition(
                UIA.UIA_ControlTypePropertyId, control_type_id_for(control_type),
            ))
        if automation_id is not None:
            conditions.append(self._uia.CreatePropertyCondition(UIA.UIA_AutomationIdPropertyId, automation_id))
        if not conditions:
            raise ValueError("find_matching_elements richiede almeno un criterio (name/control_type/automation_id)")
        combined = conditions[0]
        for extra_condition in conditions[1:]:
            combined = self._uia.CreateAndCondition(combined, extra_condition)
        results = root.FindAll(UIA.TreeScope_Descendants, combined)
        return [results.GetElement(i) for i in range(results.Length)]
