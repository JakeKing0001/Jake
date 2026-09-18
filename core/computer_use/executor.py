"""Executor semantico (F3.4.1/F3.4.4, prima fetta di F3.4 - "Executor semantico", mai iniziata
prima d'ora - vedi ROADMAP_EXECUTION.md sezione F3.4, dipende da F3.3 e F1.3). Esegue un'azione
tramite un PATTERN UI Automation (Invoke/Value/Toggle/SelectionItem/ExpandCollapse) invece di
simulare click/digitazione a coordinate pixel - la richiesta arriva direttamente all'app tramite
COM, niente coordinate che si romperebbero al primo resize/spostamento finestra (lo stesso motivo
per cui `core/computer_use/selector.py` cerca per nome, F3.3.4).

**Tre buchi reali trovati verificando ExpandCollapse, Scroll E SelectionItem contro la fixture,
non ipotizzati - il terzo e' il PIU' insidioso dei tre, una vera trappola**:
- ExpandCollapse su un `QTreeWidgetItem`: il pattern e' presente (`GetCurrentPattern` lo trova,
  `Expand()`/`Collapse()` non sollevano mai) ma NON HA ALCUN EFFETTO - `CurrentExpandCollapseState`
  resta invariato prima e dopo la chiamata, verificato leggendolo esplicitamente (non assunto dal
  "successo" della chiamata COM, che di per se' non prova nulla);
- Scroll su un `QListWidget`: qui il ponte di accessibilita' di Qt e' piu' onesto -
  `IsScrollPatternAvailable` e' esplicitamente `False` (verificato leggendo la proprieta', non
  assunto), quindi `GetCurrentPattern` restituisce correttamente nessun pattern, senza fingere di
  averlo. La conseguenza pratica e' la stessa: una riga fuori dall'area visibile ("Riga 30" in una
  lista di 30) NON E' PRESENTE nell'albero UI Automation affatto (una ricerca per nome non la
  trova, verificato) finche' qualcosa non la rende visibile - e niente in UI Automation puo' farlo
  per un `QListWidget`, ne' il pattern Scroll (non disponibile) ne' `ScrollItem`/`ScrollIntoView`
  (nemmeno quello disponibile su un elemento gia' fuori vista, verificato);
- **SelectionItem su un `QListWidgetItem` (a differenza di un `TabItem` di `QTabBar`, verificato
  funzionante con una prova INDIPENDENTE - vedi `tests/test_executor.py::SelectionItemTests`, il
  checkbox della tab 2 diventa davvero raggiungibile dopo `select()` sulla sua `TabItem`, non solo
  "selected" riportato True)**: chiamare `Select()` su un elemento della lista FA CAMBIARE
  `CurrentIsSelected` da `False` a `True` (verificato leggendo la proprieta') - sembrerebbe quindi
  funzionare. Ma il bottone "Rimuovi selezionato" della fixture, la cui abilitazione dipende dal
  VERO stato di selezione di Qt (`itemSelectionChanged`, non da UI Automation), RESTA
  disabilitato dopo la stessa chiamata - verificato esplicitamente, non assunto. Lo stato riportato
  da UI Automation e quello REALE dell'applicazione si sono DESINCRONIZZATI: fidarsi del solo
  `selected` di `ElementInfo` come prova che un'azione ha avuto un effetto vero sarebbe stato un
  errore, scoperto qui perche' esisteva un secondo modo indipendente di controllare (il bottone),
  non perche' la prima verifica sembrasse sospetta. **La stessa identica lezione che il progetto
  Jake ha gia' imparato ripetutamente in F1 per le skill** (mai fidarsi del "successo" dichiarato
  da chi esegue un'azione, verificarlo in modo indipendente - `core/execution_safety.py::
  verify_effect`) - qui riscoperta per UI Automation stesso, non solo per le skill di Jake:
  un'azione UIA "riuscita" secondo UIA stesso NON e' automaticamente riuscita per l'app target.
  Per un `TreeItem` (`QTreeWidgetItem`), `CurrentIsSelected` cambia correttamente e con semantica
  di selezione singola rispettata (selezionare "Categoria A" deseleziona "Categoria B") - ma senza
  un secondo segnale indipendente come il bottone della lista, questo NON e' dichiarato verificato
  per l'effetto REALE su Qt, solo per lo stato riportato da UI Automation - onesto "non provato",
  non un'estensione ottimistica della prova gia' fatta per `TabItem`.

`expand()`/`collapse()`/`scroll_to_bottom()`/`scroll_to_top()` restano nel codice sotto (le
implementazioni sono corrette per il contratto COM in generale, non specifiche di Qt - un'app/
toolkit diversa potrebbe onorarle per davvero), ma NON sono oggi verificate funzionanti contro
QUALUNQUE albero/lista reale, solo contro se stesse come chiamata COM che non solleva (Expand) o
che fallisce onestamente con un errore chiaro (Scroll, pattern assente) - la prova esatta del
limite, non ignorata ne' nascosta, e' nei test dedicati (`tests/test_executor.py::
ExpandCollapseKnownLimitationTests`/`ScrollKnownLimitationTests`). Conferma empirica concreta e
RIPETUTA (due pattern diversi, stesso limite sottostante) del perche' la scala di ripiego di F3.5
esiste ("API/app adapter -> UIA -> browser DOM -> OCR -> vision -> coordinate"): per contenuto
virtualizzato/nascosto in un'app Qt, UI Automation da sola non basta MAI, non e' un caso isolato.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- F3.4.1 (resto): minimizzare/massimizzare/ripristinare via `SetWindowVisualState` - non ancora
  implementato (`close_window()`, aggiunto in un incremento successivo, e' l'unica azione Window
  con un effetto univocamente verificabile senza dipendere da `CurrentWindowVisualState`, un
  segnale che un ponte di accessibilita' potrebbe non onorare fedelmente, lo stesso genere di buco
  gia' trovato per ExpandCollapse/Scroll sopra - non riverificato per Window, dichiarato onesto
  invece di assunto);
- F3.4.2 (unificare in `ComputerAgent` - `core/computer_agent.py` resta INVARIATO qui: il
  collegamento tra UIA e il controllo a pixel/OCR esistente e' la scala di ripiego di F3.5, non
  affrontata in questo incremento);
- F3.4.3 (richiedere policy prima di upload/submit/send/delete/purchase - nessun collegamento a
  `core/policy_engine.py` ancora: questo modulo esegue un'azione GIA' autorizzata da chi lo
  chiama, lo stesso principio gia' seguito da `SkillRegistry.execute()` per le skill esistenti,
  che non ricontrolla la policy da solo);
- F3.4.6 (evitare doppia esecuzione sui retry - nessun meccanismo di retry ancora qui);
- F3.4.7 (dialoghi modali/focus change come eventi - nessuna gestione esplicita ancora, oltre
  alla precondizione "enabled" di F3.4.4).

`ElementActionReceipt` (F3.4.5, "produrre un ActionReceipt con elemento target e pattern usato"):
DELIBERATAMENTE un nome diverso da `core.action_ledger.ActionReceipt` (un oggetto piu' pesante,
con `trace_id`/`risk_decision`/`authorization`/`idempotency_key` - i concetti giusti per UNA
skill gia' AUTORIZZATA a livello di `JakeCore`/`TaskAgent`/`PlanExecutor`, non per una singola
chiamata di pattern UIA dentro questo executor, che non sa nulla di autorizzazione). Un futuro
collegamento al ledger (non affrontato qui) tradurrebbe questo in un INGREDIENTE dei metadati/
result di quello, non lo sostituirebbe - restano due oggetti distinti con scopi distinti, non
un refactor silenzioso del primo.

**Onesto per costruzione, non solo per convenzione**: la ricevuta viene costruita SOLO dopo che
`_require_enabled`/`_require_pattern` sono gia' passati, e viene restituita SOLO se la chiamata al
pattern COM stesso non solleva - un fallimento (`ElementNotInteractableError`, o un `COMError`
dalla chiamata al pattern) continua a propagarsi come eccezione esattamente come prima di questo
incremento, MAI una ricevuta con un campo "riuscito=False" inventato al suo posto. Questo NON e'
pero' prova che l'azione abbia avuto un EFFETTO reale sull'app target - solo che QUEL pattern e'
stato invocato su QUELL'elemento senza errori COM. La trappola di SelectionItem documentata sopra
(la chiamata "riesce" secondo UI Automation ma il bottone che dipende dallo stato VERO di Qt resta
disabilitato) si applica identica qui: la ricevuta registra il TENTATIVO, la verifica dell'effetto
reale resta una responsabilita' del chiamante (lo stesso principio gia' seguito da
`core/execution_safety.py::verify_effect` per le skill di Jake). Il testo digitato da `set_value`
NON e' incluso nella ricevuta (solo l'identita' dell'elemento target) - un campo testo libero qui
rischierebbe di far finire una password o un dato sensibile digitato dall'utente in una ricevuta
che potrebbe un giorno essere loggata (F3.6.7, "redigere password e campi sensibili", non ancora
affrontato, ma gia' evitato qui per costruzione invece di rimandato).

`close_window()` (F3.4.1, resto - il pattern Window, l'ultimo dei sette pattern dichiarati da
F3.4.1 non ancora coperto): chiude una finestra tramite `Close()`, verificato contro la fixture
VERA (non ipotizzato) con una prova indipendente FORTE - non solo che la chiamata COM non sollevi,
ma che la finestra sparisca DAVVERO dall'albero UI Automation (`find_window_by_title` solleva
`WindowNotFoundError` subito dopo) e che il PROCESSO stesso termini (`subprocess.Popen.wait()`
restituisce un codice di uscita reale, non un `terminate()` forzato dal test). A differenza di
`expand()`/`scroll_to_bottom()` sopra, qui non c'e' alcun buco: il ponte di accessibilita' di Qt
onora `Close()` correttamente, lo stesso comportamento del bottone nativo di chiusura della
finestra. Le altre azioni del pattern Window (minimizzare/massimizzare/ripristinare via
`SetWindowVisualState`) restano deliberatamente FUORI da questo incremento (vedi sopra) - dipendono
da `CurrentWindowVisualState`, un segnale non ancora verificato contro Qt."""
import time
from dataclasses import dataclass, field

import comtypes
import comtypes.client

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402 (deve seguire GetModule)

from core.computer_use.ui_automation_adapter import _CONTROL_TYPE_NAMES  # noqa: E402 (deve seguire GetModule)

# UIA_ScrollPatternNoScroll (documentato da Microsoft come -1, non una costante nominata nel
# type library generato da comtypes): passato a SetScrollPercent() per l'asse che non si vuole
# toccare, invece di un "magic number" silenzioso senza spiegazione.
_SCROLL_NO_CHANGE = -1.0


class ElementNotInteractableError(Exception):
    """F3.4.4: precondizione fallita - l'elemento e' disabilitato, o non supporta il pattern
    richiesto per l'azione (es. il pattern Invoke su un elemento che non e' un bottone/link)."""


@dataclass(frozen=True)
class ElementActionReceipt:
    """F3.4.5: COSA e' stato tentato - non prova che sia riuscito DAVVERO per l'app target (vedi
    il docstring del modulo). `element_name`/`element_automation_id`/`element_control_type` letti
    con lo stesso "onesto None" di `ElementInfo` (F3.2.3, mai un valore indovinato quando il
    provider UI Automation non li espone)."""

    action: str  # "invoke" / "set_value" / "toggle" / "select" / "expand" / "collapse" / "scroll_to_bottom" / "scroll_to_top"
    pattern: str  # "Invoke" / "Value" / "Toggle" / "SelectionItem" / "ExpandCollapse" / "Scroll"
    element_name: str | None
    element_automation_id: str | None
    element_control_type: str | None
    ts: float = field(default_factory=time.time)


def _element_identity(element) -> tuple[str | None, str | None, str | None]:
    """Nome/automation_id/control_type dell'elemento, letti con lo stesso "onesto None" di
    `UIAutomationAdapter.describe_element` (F3.2) - NON riusato direttamente da li' per non
    richiedere un'intera istanza di `UIAutomationAdapter` solo per costruire una ricevuta
    (`ActionExecutor` resta utilizzabile senza un adapter, come prima di questo incremento)."""
    try:
        name = element.CurrentName or None
    except (ValueError, comtypes.COMError):
        name = None
    try:
        automation_id = element.CurrentAutomationId or None
    except (ValueError, comtypes.COMError):
        automation_id = None
    try:
        control_type = _CONTROL_TYPE_NAMES.get(element.CurrentControlType)
    except (ValueError, comtypes.COMError):
        control_type = None
    return name, automation_id, control_type


class ActionExecutor:
    """F3.4.1: un'azione per pattern UI Automation invece di click/digitazione simulati a
    coordinate pixel. Ogni metodo rilegge `CurrentIsEnabled` al momento dell'azione (F3.4.4,
    "aggiungere precondizioni come... controllo enabled"), non si fida dello stato osservato
    quando l'elemento e' stato TROVATO (`SelectorEngine`, F3.3) - potrebbe essere cambiato nel
    frattempo, lo stesso principio "verifica al momento giusto dell'azione, non prima" gia'
    seguito altrove nel progetto (es. F1.8.1 per le mutazioni filesystem)."""

    def invoke(self, element) -> ElementActionReceipt:
        """Bottone/link/voce di menu - il pattern Invoke ("premi questo")."""
        self._require_enabled(element)
        pattern = self._require_pattern(element, UIA.UIA_InvokePatternId, UIA.IUIAutomationInvokePattern, "Invoke")
        receipt = self._receipt("invoke", "Invoke", element)
        pattern.Invoke()
        return receipt

    def set_value(self, element, text: str) -> ElementActionReceipt:
        """Campo di testo - il pattern Value ("imposta il testo a"), non digitazione tasto per
        tasto simulata."""
        self._require_enabled(element)
        pattern = self._require_pattern(element, UIA.UIA_ValuePatternId, UIA.IUIAutomationValuePattern, "Value")
        receipt = self._receipt("set_value", "Value", element)
        pattern.SetValue(text)
        return receipt

    def toggle(self, element) -> ElementActionReceipt:
        """Casella di spunta - il pattern Toggle ("cambia stato")."""
        self._require_enabled(element)
        pattern = self._require_pattern(element, UIA.UIA_TogglePatternId, UIA.IUIAutomationTogglePattern, "Toggle")
        receipt = self._receipt("toggle", "Toggle", element)
        pattern.Toggle()
        return receipt

    def select(self, element) -> ElementActionReceipt:
        """Voce di lista/albero/tab - il pattern SelectionItem ("seleziona questo"), diverso da
        Invoke: selezionare una voce non e' "premerla" (una voce puo' essere selezionabile senza
        avere alcun significato di "azione", es. una riga di una lista).

        **`CurrentIsSelected=True` dopo questa chiamata NON e' prova sufficiente che l'azione
        abbia avuto un effetto reale sull'app target** (vedi il docstring del modulo): su un
        `QListWidgetItem` di Qt lo stato riportato da UI Automation e quello VERO dell'app si
        DESINCRONIZZANO - verificato che il bottone "Rimuovi selezionato" della fixture resta
        disabilitato anche dopo una `select()` "riuscita" secondo UI Automation. Un chiamante che
        deve sapere se l'azione ha avuto effetto DAVVERO deve verificarlo in modo indipendente
        (un secondo segnale che dipende dallo stato VERO dell'app, non da UI Automation stesso) -
        lo stesso principio "mai fidarsi del successo auto-dichiarato" gia' seguito per le skill
        di Jake (`core/execution_safety.py::verify_effect`, F1)."""
        self._require_enabled(element)
        pattern = self._require_pattern(
            element, UIA.UIA_SelectionItemPatternId, UIA.IUIAutomationSelectionItemPattern, "SelectionItem",
        )
        receipt = self._receipt("select", "SelectionItem", element)
        pattern.Select()
        return receipt

    def expand(self, element) -> ElementActionReceipt:
        """Nodo di un albero (o altro elemento ExpandCollapse) - "espandi". **Non verificato
        funzionante contro un vero `QTreeWidgetItem`** (vedi il docstring del modulo): la chiamata
        non solleva mai, ma su Qt lo stato dell'elemento non cambia davvero - un buco reale del
        ponte di accessibilita' di Qt, non di questo metodo."""
        pattern = self._expand_collapse(element)
        receipt = self._receipt("expand", "ExpandCollapse", element)
        pattern.Expand()
        return receipt

    def collapse(self, element) -> ElementActionReceipt:
        """Nodo di un albero (o altro elemento ExpandCollapse) - "collassa". Stesso limite non
        verificato di `expand()` sopra."""
        pattern = self._expand_collapse(element)
        receipt = self._receipt("collapse", "ExpandCollapse", element)
        pattern.Collapse()
        return receipt

    def _expand_collapse(self, element):
        self._require_enabled(element)
        return self._require_pattern(
            element, UIA.UIA_ExpandCollapsePatternId, UIA.IUIAutomationExpandCollapsePattern, "ExpandCollapse",
        )

    def scroll_to_bottom(self, element) -> ElementActionReceipt:
        """Lista/area con scorrimento verticale - il pattern Scroll, impostato al 100% verticale
        (fondo). **Non verificato funzionante contro un vero `QListWidget`** (vedi il docstring
        del modulo): il ponte di accessibilita' di Qt riporta onestamente
        `IsScrollPatternAvailable=False` per questo widget, quindi questo metodo solleva
        `ElementNotInteractableError` invece di eseguire un'azione senza effetto (a differenza di
        `expand()`, dove Qt SI' dichiara il pattern disponibile ma poi non lo onora)."""
        pattern = self._scroll(element)
        receipt = self._receipt("scroll_to_bottom", "Scroll", element)
        pattern.SetScrollPercent(_SCROLL_NO_CHANGE, 100.0)
        return receipt

    def scroll_to_top(self, element) -> ElementActionReceipt:
        """Come `scroll_to_bottom`, verso l'inizio (0% verticale). Stesso limite non verificato."""
        pattern = self._scroll(element)
        receipt = self._receipt("scroll_to_top", "Scroll", element)
        pattern.SetScrollPercent(_SCROLL_NO_CHANGE, 0.0)
        return receipt

    def close_window(self, element) -> ElementActionReceipt:
        """Finestra di primo livello (o altro elemento Window) - "chiudi". A differenza di
        `expand()`/`scroll_to_bottom()`, VERIFICATO funzionante contro un vero processo Qt (vedi
        il docstring del modulo): la finestra sparisce davvero dall'albero UI Automation e il
        processo stesso termina, non solo che `Close()` non sollevi."""
        self._require_enabled(element)
        pattern = self._require_pattern(element, UIA.UIA_WindowPatternId, UIA.IUIAutomationWindowPattern, "Window")
        receipt = self._receipt("close_window", "Window", element)
        pattern.Close()
        return receipt

    def _receipt(self, action: str, pattern_name: str, element) -> ElementActionReceipt:
        name, automation_id, control_type = _element_identity(element)
        return ElementActionReceipt(
            action=action, pattern=pattern_name,
            element_name=name, element_automation_id=automation_id, element_control_type=control_type,
        )

    def _scroll(self, element):
        self._require_enabled(element)
        return self._require_pattern(element, UIA.UIA_ScrollPatternId, UIA.IUIAutomationScrollPattern, "Scroll")

    def _require_enabled(self, element) -> None:
        try:
            enabled = bool(element.CurrentIsEnabled)
        except (ValueError, comtypes.COMError):
            raise ElementNotInteractableError(
                "impossibile leggere lo stato dell'elemento (provider UI Automation incompleto)"
            ) from None
        if not enabled:
            raise ElementNotInteractableError("l'elemento e' disabilitato")

    def _require_pattern(self, element, pattern_id, interface, pattern_name: str):
        try:
            raw_pattern = element.GetCurrentPattern(pattern_id)
        except (ValueError, comtypes.COMError):
            raw_pattern = None
        if not raw_pattern:
            raise ElementNotInteractableError(f"l'elemento non supporta il pattern {pattern_name}")
        return raw_pattern.QueryInterface(interface)
