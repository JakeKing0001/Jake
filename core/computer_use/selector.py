"""Selector engine (F3.3.1-F3.3.3, prima fetta di F3.3 - "Selector engine", mai iniziata prima
d'ora - vedi ROADMAP_EXECUTION.md sezione F3.3, dipende da F3.2). Trovare un elemento per nome/
ruolo/automation id invece che per coordinate assolute (F3.3.4, "salvare selector procedurali
senza coordinate assolute" - un click a coordinate fisse si rompe al primo resize/spostamento
finestra, un selettore che cerca per NOME sopravvive).

`wait_for_unique_element` (F3.4.7, adozione - "gestire dialoghi modali... come eventi, non sleep
fissi"): polling con timeout, non un singolo tentativo ottimistico ne' uno `sleep()` fisso -
generalizza lo stesso principio gia' usato da `UIAutomationAdapter.find_window_by_title` (F3.2) a
QUALUNQUE elemento, non solo una finestra di primo livello. Motivato da un buco reale trovato
verificando il flusso di conferma della fixture (F3.1.1) end-to-end: un dialogo `QMessageBox`
modale e' una VERA finestra top-level separata secondo `win32gui.EnumWindows` (usato da
`core/vision/screen.py::list_open_window_titles`), ma nell'albero di CONTROLLO di UI Automation
compare come DISCENDENTE della finestra genitrice, non come figlio del desktop - verificato
cercandolo in entrambi i modi, non assunto. `find_window_by_title` (che cerca solo tra i figli
DIRETTI del desktop) non l'avrebbe mai trovato.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- F3.3.1 (resto - "window" CHIUSO in un incremento successivo, 19/09/2026): `ElementSelector.
  window_title_contains` + `SelectorEngine.locate()` - vedi le loro docstring. Restano aperti
  "app/process" (nessun criterio per PID/nome eseguibile ancora) e "ancestor" (nessun modo di
  richiedere un antenato specifico oltre a scegliere manualmente il `root` giusto);
- F3.3.1 (resto - "app/process" CHIUSO in un incremento successivo, 20/09/2026): `ElementSelector.
  process_id` - vedi il proprio docstring. Resta aperto solo "ancestor" (nessun modo di richiedere
  un antenato specifico oltre a scegliere manualmente il `root` giusto - un incremento a se',
  dato che "un antenato" e' un ELEMENTO, non uno scalare come un PID, e richiederebbe decidere
  COME quell'antenato viene identificato, lo stesso genere di domanda che questo stesso modulo
  risolve per l'elemento finale);
- F3.3.2 (CHIUSA in un incremento successivo, 19/09/2026): il caso NoMatchError spiega QUALE
  criterio sta escludendo tutto (`_explain_no_match`), il caso gemello AmbiguousSelectionError
  elenca invece OGNI candidato trovato (`_describe_ambiguous_matches`, automation_id/bounds) -
  entrambi diagnostici, nessuno dei due cambia QUALI elementi vengono considerati un match (resta
  un'uguaglianza esatta per criterio, mai un vero punteggio/ranking fuzzy tra candidati diversi -
  quello resterebbe un cambiamento di comportamento, non solo diagnostico, non affrontato qui);
- F3.3.4 (resto - CHIUSO in un incremento successivo, 19/09/2026): `ElementSelector.to_dict()`/
  `.from_dict()` - vedi le loro docstring;
- F3.3.5 (invalidare selettori quando la struttura/versione dell'app cambia - non c'e' ancora
  nessuna cache di selettori da invalidare);
- F3.3.6 (inspector nell'HUD);
- F3.3.7 (resto - "reorder", CHIUSO in un incremento successivo, 20/09/2026): vedi
  `tests/test_selector.py::LocalizationAfterReorderTests`. Restano aperti "traduzione" (nessuna
  build multilingua della fixture) e "tema" (nessuna variazione di tema testata)."""
import time
from dataclasses import dataclass

from core.computer_use.ui_automation_adapter import ElementInfo, UIAutomationAdapter


@dataclass(frozen=True)
class ElementSelector:
    """F3.3.1: i tre criteri gia' disponibili in `ElementInfo` (F3.2.3) - `name`/`control_type`/
    `automation_id`, tutti opzionali individualmente ma ALMENO uno richiesto (un selettore senza
    alcun criterio troverebbe "qualunque elemento", non e' un selettore).

    `window_title_contains` (F3.3.1 resto - "selettori per... window"): un quarto criterio
    OPZIONALE che non conta per il "almeno uno" sopra (da solo non identifica alcun ELEMENTO,
    solo la finestra in cui cercarlo) - motivato direttamente da F3.3.4 (la serializzazione
    appena costruita): un selettore SALVATO su disco e poi ricaricato in una sessione futura non
    ha piu' un `root` gia' risolto a portata di mano come lo ha un chiamante che lo costruisce al
    volo - senza questo campo, un selettore procedurale sarebbe "procedurale" solo per
    l'ELEMENTO, non per la finestra che lo contiene, lasciando comunque al chiamante il compito
    di ritrovare la finestra giusta con codice separato. Con questo campo, `SelectorEngine.locate`
    puo' fare ENTRAMBI i passi da un solo `ElementSelector` auto-sufficiente.

    `process_id` (F3.3.1 resto - "selettori per... app/process"): un quinto criterio OPZIONALE,
    NON conta per il "almeno uno" sopra (un PID da solo non identifica alcun elemento specifico,
    solo il PROCESSO a cui deve appartenere) - ma a differenza di `window_title_contains`,
    DELIBERATAMENTE ESCLUSO da `to_dict`/`from_dict` (vedi i loro docstring): un PID e' un valore
    EFFIMERO, valido solo finche' vive il processo che lo ha ricevuto da Windows al lancio -
    salvarlo su disco e ricaricarlo in una sessione futura non ritroverebbe mai lo stesso
    processo (rilanciato, avrebbe un PID diverso), o peggio potrebbe far combaciare per puro caso
    un processo COMPLETAMENTE DIVERSO a cui Windows ha nel frattempo riassegnato lo stesso numero
    - un rischio di corrispondenza SBAGLIATA, non solo di nessuna corrispondenza. Il caso
    motivante di questo campo e' quindi solo IN SESSIONE, con un PID appena risolto (es.
    `subprocess.Popen(...).pid`, o `IsolatedBrowserProcess.process.pid` gia' usato da F3.6) per
    distinguere due finestre/processi diversi che espongono elementi con lo stesso `name`/
    `control_type` - mai attraverso un salvataggio/ricaricamento."""

    name: str | None = None
    control_type: str | None = None
    automation_id: str | None = None
    window_title_contains: str | None = None
    process_id: int | None = None

    def __post_init__(self) -> None:
        if self.name is None and self.control_type is None and self.automation_id is None:
            raise ValueError("ElementSelector richiede almeno un criterio (name/control_type/automation_id)")

    def to_dict(self) -> dict:
        """F3.3.4 (resto - "salvare selector procedurali senza coordinate assolute"): solo i campi
        NON `None`, cosi' un selettore salvato su disco (JSON, YAML, o qualunque formato un
        chiamante scelga - questo metodo resta agnostico rispetto al formato, restituisce solo un
        dict semplice) e poi ricaricato produce lo STESSO `ElementSelector`, non uno con campi
        `None` scritti esplicitamente che renderebbero il file piu' rumoroso senza aggiungere
        informazione (un criterio omesso e un criterio `None` significano gia' la stessa cosa in
        questa classe).

        `process_id` e' l'UNICO campo MAI incluso qui, anche quando non e' `None` - vedi il
        docstring della classe: e' un valore effimero (un PID rilanciato non e' piu' lo stesso
        processo), serializzarlo produrrebbe un selettore che al ricaricamento non trova mai piu'
        nulla, o peggio trova per caso il processo sbagliato."""
        result: dict = {}
        if self.name is not None:
            result["name"] = self.name
        if self.control_type is not None:
            result["control_type"] = self.control_type
        if self.automation_id is not None:
            result["automation_id"] = self.automation_id
        if self.window_title_contains is not None:
            result["window_title_contains"] = self.window_title_contains
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ElementSelector":
        """L'inverso di `to_dict` - solleva `ValueError` (non ignora silenziosamente) su una
        chiave SCONOSCIUTA, non solo sull'assenza di ogni criterio gia' coperta da
        `__post_init__`: un selettore procedurale salvato e poi ricaricato con un campo
        scritto male o di uno schema futuro non ancora supportato deve fallire RUMOROSAMENTE,
        non produrre silenziosamente un selettore PIU' AMPIO di quello inteso (un criterio perso
        per un typo aumenterebbe il rischio di un match ambiguo o, peggio, di un match SBAGLIATO
        su un elemento diverso da quello originariamente salvato - lo stesso principio "rifiuta
        l'ambiguita'/l'incertezza invece di indovinare" gia' seguito da `find_unique`). `process_id`
        e' RIFIUTATO qui come qualunque altra chiave sconosciuta, deliberatamente - `to_dict` non
        lo scrive mai (vedi il proprio docstring), quindi comparirebbe qui solo scritto a mano da
        un chiamante che ignora perche' e' effimero: lo stesso errore rumoroso, non un'eccezione
        dedicata che implicherebbe un supporto parziale non voluto."""
        unknown_keys = set(data) - {"name", "control_type", "automation_id", "window_title_contains"}
        if unknown_keys:
            raise ValueError(f"ElementSelector.from_dict: chiavi sconosciute {sorted(unknown_keys)}")
        return cls(
            name=data.get("name"), control_type=data.get("control_type"),
            automation_id=data.get("automation_id"), window_title_contains=data.get("window_title_contains"),
        )


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

    def _explain_no_match(self, root, selector: ElementSelector) -> str:
        """F3.3.2 (prima fetta - "spiegare perche' un elemento e' stato scelto", qui il caso
        gemello "perche' NESSUNO lo e' stato"): per ogni criterio dato SINGOLARMENTE, quanti
        elementi lo soddisfano DA SOLO - rivela quale criterio specifico sta escludendo tutto
        (es. un `name` con un refuso: 0 elementi per quello, N>0 per `control_type` dato insieme)
        invece del solo messaggio opaco "nessun elemento corrisponde". Chiamata SOLO nel percorso
        di fallimento gia' raggiunto (mai sul percorso di successo, ne' a ogni iterazione di
        `wait_for_unique_element` - il costo di query aggiuntive e' accettabile solo una volta,
        dopo che la ricerca combinata e' gia' fallita/scaduta)."""
        parts = []
        if selector.name is not None:
            count = len(self._adapter.find_matching_elements(root, name=selector.name))
            parts.append(f"{count} con name={selector.name!r}")
        if selector.control_type is not None:
            count = len(self._adapter.find_matching_elements(root, control_type=selector.control_type))
            parts.append(f"{count} con control_type={selector.control_type!r}")
        if selector.automation_id is not None:
            count = len(self._adapter.find_matching_elements(root, automation_id=selector.automation_id))
            parts.append(f"{count} con automation_id={selector.automation_id!r}")
        if selector.process_id is not None:
            count = len(self._adapter.find_matching_elements(root, process_id=selector.process_id))
            parts.append(f"{count} con process_id={selector.process_id!r}")
        return "trovati singolarmente: " + "; ".join(parts)

    def _describe_ambiguous_matches(self, matches) -> str:
        """F3.3.2 (resto - "spiegare perche' un elemento e' stato scelto tra piu' candidati", qui
        il caso gemello di `_explain_no_match`: non "perche' nessuno", ma "quali sono i troppi").
        Una breve descrizione di OGNI candidato (`automation_id`/`bounds`, i due segnali piu'
        utili per capire come restringere ulteriormente il selettore) invece del solo conteggio -
        accetta sia `ElementInfo` gia' descritti (da `find_all`, usato da `find_unique`) sia
        elementi COM GREZZI (`wait_for_unique_element`/`find_unique_element`, che non passano da
        `find_all`), descrivendo questi ultimi al volo con lo stesso `describe_element` gia' usato
        ovunque nel modulo - nessuna duplicazione della logica "onesto None su un provider
        incompleto" gia' costruita li'."""
        descriptions = []
        for match in matches:
            info = match if isinstance(match, ElementInfo) else self._adapter.describe_element(match)
            if info is None:
                descriptions.append("<non leggibile>")
            else:
                descriptions.append(f"automation_id={info.automation_id!r} bounds={info.bounds}")
        return "; ".join(descriptions)

    def find_all(self, root, selector: ElementSelector) -> list[ElementInfo]:
        """Tutti gli elementi tra i discendenti di `root` che soddisfano il selettore, gia'
        descritti (`ElementInfo`, F3.2.3) - un elemento trovato ma non leggibile (lo stesso
        genere di provider incompleto gia' documentato in `UIAutomationAdapter.describe_element`)
        viene semplicemente OMESSO dal risultato, non fa fallire l'intera ricerca."""
        raw_matches = self._adapter.find_matching_elements(
            root, name=selector.name, control_type=selector.control_type,
            automation_id=selector.automation_id, process_id=selector.process_id,
        )
        described = (self._adapter.describe_element(element) for element in raw_matches)
        return [info for info in described if info is not None]

    def find_unique(self, root, selector: ElementSelector) -> ElementInfo:
        """F3.3.3: l'API SICURA per un'azione che deve colpire esattamente un elemento - solleva
        `NoMatchError`/`AmbiguousSelectionError` invece di restituire un candidato indovinato
        quando la ricerca non produce esattamente un risultato."""
        matches = self.find_all(root, selector)
        if not matches:
            raise NoMatchError(f"nessun elemento corrisponde a {selector!r} ({self._explain_no_match(root, selector)})")
        if len(matches) > 1:
            raise AmbiguousSelectionError(
                f"{len(matches)} elementi corrispondono a {selector!r}: servono criteri piu' precisi "
                f"({self._describe_ambiguous_matches(matches)})"
            )
        return matches[0]

    def wait_for_unique_element(self, root, selector: ElementSelector, timeout_seconds: float = 5.0):
        """F3.4.7 (adozione): come `find_unique_element`, ma RITENTA con un breve intervallo fino
        al timeout invece di un singolo tentativo - per un elemento che potrebbe non essere
        ancora presente nell'albero (es. un dialogo modale appena aperto, vedi il docstring del
        modulo). Un'ambiguita' (piu' di un match) fa fallire SUBITO, non dopo il timeout - aspettare
        non la risolverebbe mai da sola, e' un problema del selettore, non di tempismo."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            matches = self._adapter.find_matching_elements(
                root, name=selector.name, control_type=selector.control_type,
                automation_id=selector.automation_id, process_id=selector.process_id,
            )
            if len(matches) > 1:
                raise AmbiguousSelectionError(
                    f"{len(matches)} elementi corrispondono a {selector!r}: servono criteri piu' precisi "
                    f"({self._describe_ambiguous_matches(matches)})"
                )
            if len(matches) == 1:
                return matches[0]
            if time.monotonic() >= deadline:
                raise NoMatchError(
                    f"nessun elemento corrisponde a {selector!r} entro {timeout_seconds}s "
                    f"({self._explain_no_match(root, selector)})"
                )
            time.sleep(0.1)

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
            automation_id=selector.automation_id, process_id=selector.process_id,
        )
        if not matches:
            raise NoMatchError(f"nessun elemento corrisponde a {selector!r} ({self._explain_no_match(root, selector)})")
        if len(matches) > 1:
            raise AmbiguousSelectionError(
                f"{len(matches)} elementi corrispondono a {selector!r}: servono criteri piu' precisi "
                f"({self._describe_ambiguous_matches(matches)})"
            )
        return matches[0]

    def locate(self, selector: ElementSelector, timeout_seconds: float = 5.0):
        """F3.3.1 (resto - "selettori per... window"): come `wait_for_unique_element`, ma senza
        bisogno di un `root` gia' risolto dal chiamante - richiede invece che `selector.
        window_title_contains` sia dato, e trova PRIMA la finestra (`UIAutomationAdapter.
        find_window_by_title_containing`, F3.7) poi l'elemento al suo interno, con un SOLO
        `ElementSelector` auto-sufficiente. Il caso motivante e' un selettore RICARICATO da
        `ElementSelector.from_dict` (F3.3.4) in una sessione futura, senza alcun `root` gia' in
        memoria da una ricerca precedente - senza questo metodo, un selettore salvato resterebbe
        "procedurale" solo per l'elemento, non per la finestra che lo contiene.

        `ValueError` (non un `NoMatchError` fuorviante) se `window_title_contains` non e' dato -
        un errore di programmazione del chiamante (ha dimenticato il criterio della finestra), non
        un fallimento della ricerca stessa, merita un'eccezione diversa e piu' chiara. La ricerca
        della finestra e quella dell'elemento condividono lo stesso `timeout_seconds` totale, non
        raddoppiato - una finestra che impiega quasi tutto il timeout a comparire lascerebbe
        volutamente poco tempo all'elemento, invece di sommare due attese indipendenti che
        potrebbero far restare bloccata la chiamata per il doppio del timeout dichiarato."""
        if selector.window_title_contains is None:
            raise ValueError("SelectorEngine.locate richiede selector.window_title_contains")
        deadline = time.monotonic() + timeout_seconds
        window = self._adapter.find_window_by_title_containing(
            selector.window_title_contains, timeout_seconds=timeout_seconds,
        )
        remaining = max(0.0, deadline - time.monotonic())
        return self.wait_for_unique_element(window, selector, timeout_seconds=remaining)
