"""Selector engine (F3.3.1-F3.3.3, prima fetta di F3.3 - "Selector engine", mai iniziata prima
d'ora - vedi ROADMAP_EXECUTION.md sezione F3.3, dipende da F3.2). Trovare un elemento per nome/
ruolo/automation id invece che per coordinate assolute (F3.3.4, "salvare selector procedurali
senza coordinate assolute" - un click a coordinate fisse si rompe al primo resize/spostamento
finestra, un selettore che cerca per NOME sopravvive).

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- F3.3.1 (resto): selettori per app/process, window, ancestor - solo name/control_type/
  automation_id qui, gia' le tre proprieta' che `ElementInfo` (F3.2.3) espone oggi;
- F3.3.2: un vero punteggio/spiegazione del PERCHE' un elemento e' stato scelto tra piu'
  candidati - oggi `find_unique` rifiuta l'ambiguita' invece di sceglierne uno (F3.3.3), ma non
  assegna ancora un punteggio a candidati diversi;
- F3.3.4 (resto): salvare/serializzare un selettore (oggi e' gia' un dataclass, quindi gia'
  "procedurale senza coordinate", ma non c'e' ancora un formato di salvataggio su disco);
- F3.3.5 (invalidare selettori quando la struttura/versione dell'app cambia - non c'e' ancora
  nessuna cache di selettori da invalidare);
- F3.3.6 (inspector nell'HUD);
- F3.3.7 (testare la localizzazione dopo resize/reorder/traduzione/tema - non ancora testato
  esplicitamente, anche se l'uso di NOME invece di coordinate lo rende plausibile per
  costruzione)."""
from dataclasses import dataclass

from core.computer_use.ui_automation_adapter import ElementInfo, UIAutomationAdapter


@dataclass(frozen=True)
class ElementSelector:
    """F3.3.1: i tre criteri gia' disponibili in `ElementInfo` (F3.2.3) - `name`/`control_type`/
    `automation_id`, tutti opzionali individualmente ma ALMENO uno richiesto (un selettore senza
    alcun criterio troverebbe "qualunque elemento", non e' un selettore)."""

    name: str | None = None
    control_type: str | None = None
    automation_id: str | None = None

    def __post_init__(self) -> None:
        if self.name is None and self.control_type is None and self.automation_id is None:
            raise ValueError("ElementSelector richiede almeno un criterio (name/control_type/automation_id)")


class NoMatchError(Exception):
    """Nessun elemento corrisponde al selettore dato."""


class AmbiguousSelectionError(Exception):
    """F3.3.3: piu' di un elemento corrisponde al selettore - rifiutata la scelta invece di
    prenderne uno a caso (es. il primo trovato, comunque arbitrario dal punto di vista di chi ha
    scritto il selettore). Un'azione ad alto impatto (F3.4, non ancora costruita) non deve MAI
    agire su un candidato scelto per caso tra piu' possibilita' ambigue."""


class SelectorEngine:
    """F3.3.1: dietro la propria interfaccia, sopra `UIAutomationAdapter` (F3.2) - non lega
    direttamente le skill a `comtypes`/`UIAutomationClient`, solo a questo e all'adapter."""

    def __init__(self, adapter: UIAutomationAdapter) -> None:
        self._adapter = adapter

    def find_all(self, root, selector: ElementSelector) -> list[ElementInfo]:
        """Tutti gli elementi tra i discendenti di `root` che soddisfano il selettore, gia'
        descritti (`ElementInfo`, F3.2.3) - un elemento trovato ma non leggibile (lo stesso
        genere di provider incompleto gia' documentato in `UIAutomationAdapter.describe_element`)
        viene semplicemente OMESSO dal risultato, non fa fallire l'intera ricerca."""
        raw_matches = self._adapter.find_matching_elements(
            root, name=selector.name, control_type=selector.control_type,
            automation_id=selector.automation_id,
        )
        described = (self._adapter.describe_element(element) for element in raw_matches)
        return [info for info in described if info is not None]

    def find_unique(self, root, selector: ElementSelector) -> ElementInfo:
        """F3.3.3: l'API SICURA per un'azione che deve colpire esattamente un elemento - solleva
        `NoMatchError`/`AmbiguousSelectionError` invece di restituire un candidato indovinato
        quando la ricerca non produce esattamente un risultato."""
        matches = self.find_all(root, selector)
        if not matches:
            raise NoMatchError(f"nessun elemento corrisponde a {selector!r}")
        if len(matches) > 1:
            raise AmbiguousSelectionError(
                f"{len(matches)} elementi corrispondono a {selector!r}: servono criteri piu' precisi"
            )
        return matches[0]

    def find_unique_element(self, root, selector: ElementSelector):
        """Come `find_unique`, ma restituisce l'elemento COM GREZZO (non `ElementInfo`) -
        `core/computer_use/executor.py` (F3.4) ne ha bisogno per AGIRE su un elemento (i pattern
        UI Automation si invocano sull'elemento COM vero), non solo per osservarlo.

        Stessa logica "rifiuta l'ambiguita'" di `find_unique`, ma valutata sui match GREZZI
        (prima del filtro "leggibile" di `describe_element`) - le due funzioni NON sono state
        unificate per non cambiare il comportamento gia' testato di `find_unique`/`find_all` su
        un caso limite raro (un match grezzo non leggibile mescolato a uno leggibile: la versione
        gia' spedita conta solo i leggibili, questa conterebbe anche quello illeggibile come
        ambiguita' - una scelta deliberatamente diversa, non un refactor silenzioso della prima)."""
        matches = self._adapter.find_matching_elements(
            root, name=selector.name, control_type=selector.control_type,
            automation_id=selector.automation_id,
        )
        if not matches:
            raise NoMatchError(f"nessun elemento corrisponde a {selector!r}")
        if len(matches) > 1:
            raise AmbiguousSelectionError(
                f"{len(matches)} elementi corrispondono a {selector!r}: servono criteri piu' precisi"
            )
        return matches[0]
