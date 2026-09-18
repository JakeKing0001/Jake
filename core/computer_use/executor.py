"""Executor semantico (F3.4.1/F3.4.4, prima fetta di F3.4 - "Executor semantico", mai iniziata
prima d'ora - vedi ROADMAP_EXECUTION.md sezione F3.4, dipende da F3.3 e F1.3). Esegue un'azione
tramite un PATTERN UI Automation (Invoke/Value/Toggle/SelectionItem/ExpandCollapse) invece di
simulare click/digitazione a coordinate pixel - la richiesta arriva direttamente all'app tramite
COM, niente coordinate che si romperebbero al primo resize/spostamento finestra (lo stesso motivo
per cui `core/computer_use/selector.py` cerca per nome, F3.3.4).

**Buco reale trovato verificando ExpandCollapse contro la fixture, non ipotizzato**: a differenza
di Invoke/Value/Toggle/SelectionItem (tutti e quattro verificati funzionanti DAVVERO - lo stato
cambia sul serio, non solo "la chiamata non solleva"), il pattern ExpandCollapse su un
`QTreeWidgetItem` di Qt e' presente (`GetCurrentPattern` lo trova, `Expand()`/`Collapse()` non
sollevano mai un errore) ma NON HA ALCUN EFFETTO: `CurrentExpandCollapseState` resta invariato
prima e dopo la chiamata, verificato leggendolo esplicitamente (non assunto dal "successo" della
chiamata) - il ponte di accessibilita' di Qt implementa l'INTERFACCIA del pattern senza
implementarne il COMPORTAMENTO per questo widget. `expand()`/`collapse()` restano nel codice
sotto (l'implementazione e' corretta per il contratto COM in generale, non specifica di Qt - una
diversa app/toolkit potrebbe onorarla per davvero), ma NON sono oggi verificati funzionanti contro
QUALUNQUE albero reale, solo contro se stessi come chiamata COM che non solleva - la prova esatta
del limite, non ignorato ne' nascosto, e' nel test dedicato (`tests/test_executor.py::
ExpandCollapseKnownLimitationTests`). Conferma empirica concreta del perche' la scala di ripiego
di F3.5 esiste ("API/app adapter -> UIA -> browser DOM -> OCR -> vision -> coordinate"): UI
Automation da sola non basta sempre, anche quando il pattern giusto e' formalmente presente.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- F3.4.1 (resto): Scroll e Window pattern - non ancora implementati (Invoke/Value/Toggle/
  SelectionItem coprono gia' quattro dei cinque task dichiarati da F3.1.2, ma non ancora "scorri
  fino in fondo"; ExpandCollapse e' implementato ma non verificato funzionante, vedi sopra);
- F3.4.2 (unificare in `ComputerAgent` - `core/computer_agent.py` resta INVARIATO qui: il
  collegamento tra UIA e il controllo a pixel/OCR esistente e' la scala di ripiego di F3.5, non
  affrontata in questo incremento);
- F3.4.3 (richiedere policy prima di upload/submit/send/delete/purchase - nessun collegamento a
  `core/policy_engine.py` ancora: questo modulo esegue un'azione GIA' autorizzata da chi lo
  chiama, lo stesso principio gia' seguito da `SkillRegistry.execute()` per le skill esistenti,
  che non ricontrolla la policy da solo);
- F3.4.5 (produrre un `ActionReceipt` con elemento target e pattern usato - nessun collegamento
  al ledger ancora);
- F3.4.6 (evitare doppia esecuzione sui retry - nessun meccanismo di retry ancora qui);
- F3.4.7 (dialoghi modali/focus change come eventi - nessuna gestione esplicita ancora, oltre
  alla precondizione "enabled" di F3.4.4)."""
import comtypes
import comtypes.client

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402 (deve seguire GetModule)


class ElementNotInteractableError(Exception):
    """F3.4.4: precondizione fallita - l'elemento e' disabilitato, o non supporta il pattern
    richiesto per l'azione (es. il pattern Invoke su un elemento che non e' un bottone/link)."""


class ActionExecutor:
    """F3.4.1: un'azione per pattern UI Automation invece di click/digitazione simulati a
    coordinate pixel. Ogni metodo rilegge `CurrentIsEnabled` al momento dell'azione (F3.4.4,
    "aggiungere precondizioni come... controllo enabled"), non si fida dello stato osservato
    quando l'elemento e' stato TROVATO (`SelectorEngine`, F3.3) - potrebbe essere cambiato nel
    frattempo, lo stesso principio "verifica al momento giusto dell'azione, non prima" gia'
    seguito altrove nel progetto (es. F1.8.1 per le mutazioni filesystem)."""

    def invoke(self, element) -> None:
        """Bottone/link/voce di menu - il pattern Invoke ("premi questo")."""
        self._require_enabled(element)
        pattern = self._require_pattern(element, UIA.UIA_InvokePatternId, UIA.IUIAutomationInvokePattern, "Invoke")
        pattern.Invoke()

    def set_value(self, element, text: str) -> None:
        """Campo di testo - il pattern Value ("imposta il testo a"), non digitazione tasto per
        tasto simulata."""
        self._require_enabled(element)
        pattern = self._require_pattern(element, UIA.UIA_ValuePatternId, UIA.IUIAutomationValuePattern, "Value")
        pattern.SetValue(text)

    def toggle(self, element) -> None:
        """Casella di spunta - il pattern Toggle ("cambia stato")."""
        self._require_enabled(element)
        pattern = self._require_pattern(element, UIA.UIA_TogglePatternId, UIA.IUIAutomationTogglePattern, "Toggle")
        pattern.Toggle()

    def select(self, element) -> None:
        """Voce di lista/albero/tab - il pattern SelectionItem ("seleziona questo"), diverso da
        Invoke: selezionare una voce non e' "premerla" (una voce puo' essere selezionabile senza
        avere alcun significato di "azione", es. una riga di una lista)."""
        self._require_enabled(element)
        pattern = self._require_pattern(
            element, UIA.UIA_SelectionItemPatternId, UIA.IUIAutomationSelectionItemPattern, "SelectionItem",
        )
        pattern.Select()

    def expand(self, element) -> None:
        """Nodo di un albero (o altro elemento ExpandCollapse) - "espandi". **Non verificato
        funzionante contro un vero `QTreeWidgetItem`** (vedi il docstring del modulo): la chiamata
        non solleva mai, ma su Qt lo stato dell'elemento non cambia davvero - un buco reale del
        ponte di accessibilita' di Qt, non di questo metodo."""
        self._expand_collapse(element).Expand()

    def collapse(self, element) -> None:
        """Nodo di un albero (o altro elemento ExpandCollapse) - "collassa". Stesso limite non
        verificato di `expand()` sopra."""
        self._expand_collapse(element).Collapse()

    def _expand_collapse(self, element):
        self._require_enabled(element)
        return self._require_pattern(
            element, UIA.UIA_ExpandCollapsePatternId, UIA.IUIAutomationExpandCollapsePattern, "ExpandCollapse",
        )

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
