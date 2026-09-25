"""Procedure semantiche (F3.8.1, prima fetta di F3.8 - "Learn by demonstration", mai iniziata
prima d'ora - vedi ROADMAP_EXECUTION.md sezione F3.8). F3.8 dichiara "Dipende da: F3.3, F3.4 e F5
procedural memory" - F5 (memoria procedurale) NON esiste ancora in questo progetto, quindi questo
modulo affronta SOLO il pezzo che non dipende da F5: "registrare azioni semantiche, non video o
coordinate grezze" (F3.8.1) come una struttura dati REGISTRABILE/RIGIOCABILE, senza ancora
decidere DOVE/COME una procedura completa viene salvata a lungo termine (quello e' F3.8.5,
"salvare versione, app target, selector e undo" - un incremento futuro, quando F5 esistera').

Questo e' esattamente il principio "un incremento alla volta" gia' seguito per
`ElementSelector.to_dict()`/`.from_dict()` (F3.3.4): quel metodo non ha deciso il formato di
persistenza (JSON su disco? un database?), solo la forma serializzabile. Qui allo stesso modo:
`RecordedStep` e' la forma serializzabile di UN passo, `replay_step`/`replay_steps` la rigiocano
DAVVERO contro un'app vera - nessuna decisione su dove una LISTA di `RecordedStep` (una procedura
completa) viva a lungo termine.

**"Semantiche, non coordinate grezze" e' gia' garantito per costruzione, non da verificare qui**:
un `RecordedStep` contiene un `ElementSelector` (F3.3.1-F3.3.4, name/control_type/automation_id/
window_title_contains) - MAI una coordinata pixel assoluta. Il replay usa `ComputerAgent.
click_element`/`type_into_element` (F3.4.2), che gia' risolvono le coordinate DAL VIVO al momento
dell'azione (F3.3.7, verificato sopravvivere a un resize reale) - una procedura registrata
sopravvive quindi a un resize/spostamento della finestra fin dal primo giorno, senza che questo
modulo debba fare nulla in piu' per garantirlo.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- F3.8.2 (prima meta' - "parametri variabili" - CHIUSA in un incremento successivo, 19/09/2026):
  `substitute_parameters()`/`MissingParameterError`, un placeholder `${nome}` in `step.text`
  sostituito a runtime da `replay_step`/`replay_steps`/`dry_run_step`/`dry_run_steps`. Resta
  aperta la seconda meta' ("precondizioni" - nessun modo di dichiarare che un passo richiede uno
  stato precedente oltre a "il selettore risolve", ne' di INFERIRE automaticamente quali parti
  del testo registrato sono variabili invece di richiedere che il chiamante le marchi a mano con
  `${nome}`);
- F3.8.3 (mostrare la procedura generalizzata all'utente - nessuna UI/HUD qui);
- F3.8.4 (prima fetta - "dry-run" - CHIUSA in un incremento successivo, 19/09/2026):
  `dry_run_step()`/`dry_run_steps()` - vedi le loro docstring. Resta aperta la seconda meta'
  ("su dati innocui" - eseguire per davvero ma contro un ambiente/dato sicuro, diverso da "non
  eseguire affatto");
- F3.8.5 (prima fetta - "salvare... selector" - CHIUSA in un incremento successivo, 19/09/2026):
  `core/procedure_manager.py::ProcedureManager` - salva/richiama una LISTA di `RecordedStep` con
  un nome, stesso principio di `WorkflowManager` (riusa la memoria a lungo termine gia'
  costruita, mai un file per nome su disco). Restano aperti "versione"/"app target"/"undo" -
  solo il nome e la lista di passi sono persistiti oggi, nessun versionamento ne' un modo di
  annullare una procedura gia' eseguita;
- F3.8.6 (prima fetta - "rilevare drift" - CHIUSA in un incremento successivo, 20/09/2026):
  `is_likely_drift()` - vedi il proprio docstring. Resta aperta la seconda meta' ("sospendere la
  routine" - nessun chiamante ancora usa questo segnale per disabilitare/segnalare una procedura
  che continua a driftare, ne' esiste un conteggio di drift consecutivi);
- F3.8.7 (richiedere nuova approvazione se capability/impatto CAMBIANO nel tempo - `core/
  policy_engine.py` E' gia' collegato per la decisione INIZIALE, F3.4.3, ma nessun meccanismo
  rileva se il rischio di una procedura gia' approvata una volta e' aumentato da quando).

**Scoperta empirica (20/09/2026), non assunta**: `RecordedStep`/`replay_steps` funzionano GIA'
contro una pagina web reale (Edge, `benchmarks/browser_fixture.html`), senza alcuna modifica a
questo modulo - vedi `tests/test_procedure.py::ReplayAgainstARealBrowserPageTests`. `replay_step`
risolve sempre la finestra per TITOLO (mai per `root=` gia' risolto) e la passa come `root=` a
`click_element`/`type_into_element` - la cui ricerca esplora TUTTI i discendenti del root, incluso
il contenuto della pagina dentro il nodo `Document`, anche quando `root` e' l'INTERA finestra del
browser (chrome + pagina), non ristretta al `Document` come fa invece
`core/computer_use/browser_adapter.py::find_page_document`. Un limite reale, non solo un
successo: cercare sull'intera finestra (non solo sul `Document`) rende un selettore per SOLO
`name`/`control_type` (senza `automation_id`) teoricamente esposto a collidere con un elemento del
chrome del browser - non osservato con la fixture attuale (automation_id univoci), non
ulteriormente mitigato qui."""
import re
from dataclasses import dataclass

from core.computer_agent import ComputerActionResult, ComputerAgent
from core.computer_use.selector import ElementSelector
from core.computer_use.ui_automation_adapter import UIAutomationAdapter

ACTION_CLICK = "click"
ACTION_TYPE = "type"
_KNOWN_ACTIONS = frozenset({ACTION_CLICK, ACTION_TYPE})

_PARAMETER_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class MissingParameterError(Exception):
    """F3.8.2 ("inferire parametri variabili... " - qui la META' realizzata: SOSTITUIRE un
    parametro gia' nominato in fase di registrazione, non ancora INFERIRLO automaticamente da un
    esempio, vedi il docstring del modulo). Un placeholder `${nome}` nel `text` di un passo senza
    un valore corrispondente in `parameters` deve fallire RUMOROSAMENTE - MAI scrivere il
    placeholder letterale (`"${username}"`) in un campo reale, che per un modulo di login o un
    modulo con dati sensibili sarebbe un errore osservabile solo dopo il fatto, non prima."""


def substitute_parameters(text: str, parameters: dict[str, str] | None) -> str:
    """Sostituisce ogni `${nome}` in `text` con `parameters[nome]` - un `text` SENZA alcun
    placeholder passa invariato anche se `parameters` e' vuoto/`None` (la maggioranza dei passi
    REGISTRATI oggi, F3.8.1, sono ancora valori letterali puri, F3.8.2 li rende OPZIONALMENTE
    parametrici, non li trasforma tutti in template). Il pattern (`${nome}`, lettere/cifre/
    underscore, deve iniziare con lettera o underscore) e' deliberatamente lo stesso stile gia'
    familiare da shell/template comuni, non un formato inventato per questo modulo."""
    parameters = parameters or {}

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in parameters:
            raise MissingParameterError(f"parametro mancante: {name!r} nel testo {text!r}")
        return parameters[name]

    return _PARAMETER_PATTERN.sub(_replace, text)


@dataclass(frozen=True)
class RecordedStep:
    """Un passo REGISTRATO: QUALE azione (`action`), SU QUALE elemento (`selector`, mai una
    coordinata), CON QUALE testo (`text`, solo per `ACTION_TYPE` - `None` per `ACTION_CLICK`, un
    valore letterale o parametrico con `${nome}`, F3.8.2, sostituito a runtime da
    `substitute_parameters`). `selector.window_title_contains` e' OBBLIGATORIO qui (a differenza
    di `ElementSelector` da sola, dove resta opzionale) - un passo REGISTRATO deve poter essere
    rigiocato in una sessione futura senza alcuna finestra gia' risolta a portata di mano, lo
    stesso motivo che ha gia' portato a costruire quel campo in F3.3.1.

    `risk_intent` (F3.4.3, adozione - opzionale, `None` di default): il nome di intent (lo stesso
    vocabolario gia' censito in `core/risk.py`/`core/policy_engine.py`, es. "DELETE_PATH") che
    QUESTO passo rappresenta in termini di rischio - dichiarato ESPLICITAMENTE da chi registra la
    procedura, mai indovinato da `ComputerAgent` dal testo del bottone cliccato (un'euristica
    fragile e dipendente dalla lingua, deliberatamente scartata - vedi il docstring di
    `core/computer_agent.py`). `replay_step`/`dry_run_step` lo inoltrano a `ComputerAgent`, che
    decide davvero se procedere."""

    action: str
    selector: ElementSelector
    text: str | None = None
    risk_intent: str | None = None
    # F3.8.5: il passo che annulla QUESTO passo, se l'utente/la registrazione lo conosce (es. il
    # click su "Rimuovi" per un click su "Aggiungi"). Un solo livello: un passo di annullamento non
    # ha a sua volta un annullamento. None = nessun annullamento noto (dichiarato, mai inventato).
    undo: "RecordedStep | None" = None

    def __post_init__(self) -> None:
        if self.action not in _KNOWN_ACTIONS:
            raise ValueError(f"RecordedStep.action sconosciuta: {self.action!r} (attese: {sorted(_KNOWN_ACTIONS)})")
        if self.selector.window_title_contains is None:
            raise ValueError("RecordedStep richiede selector.window_title_contains (vedi il docstring della classe)")
        if self.action == ACTION_TYPE and self.text is None:
            raise ValueError("RecordedStep con action='type' richiede 'text'")
        if self.action == ACTION_CLICK and self.text is not None:
            raise ValueError("RecordedStep con action='click' non deve avere 'text' (un click non scrive nulla)")
        if self.undo is not None and self.undo.undo is not None:
            raise ValueError("un passo di annullamento non puo' avere a sua volta un annullamento")

    def to_dict(self) -> dict:
        """Come `ElementSelector.to_dict` (F3.3.4): un dict semplice, agnostico rispetto a DOVE/
        COME un chiamante lo persiste davvero (F3.8.5, non affrontato qui)."""
        result: dict = {"action": self.action, "selector": self.selector.to_dict()}
        if self.text is not None:
            result["text"] = self.text
        if self.risk_intent is not None:
            result["risk_intent"] = self.risk_intent
        if self.undo is not None:
            result["undo"] = self.undo.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "RecordedStep":
        """L'inverso di `to_dict` - stesso principio "rifiuta invece di indovinare" gia' seguito
        da `ElementSelector.from_dict`: una chiave sconosciuta solleva `ValueError` invece di
        essere ignorata silenziosamente."""
        unknown_keys = set(data) - {"action", "selector", "text", "risk_intent", "undo"}
        if unknown_keys:
            raise ValueError(f"RecordedStep.from_dict: chiavi sconosciute {sorted(unknown_keys)}")
        if "action" not in data or "selector" not in data:
            raise ValueError("RecordedStep.from_dict richiede almeno 'action' e 'selector'")
        return cls(
            action=data["action"], selector=ElementSelector.from_dict(data["selector"]),
            risk_intent=data.get("risk_intent"),
            text=data.get("text"),
            undo=cls.from_dict(data["undo"]) if data.get("undo") is not None else None,
        )


class UnknownActionError(Exception):
    """Non dovrebbe mai accadere per un `RecordedStep` costruito tramite il costruttore/
    `from_dict` (entrambi gia' validano `action` in `__post_init__`) - esiste comunque come difesa
    esplicita in `replay_step`, coerente con "non fidarsi ciecamente" anche di un input che
    dovrebbe gia' essere valido, invece di un `AttributeError`/comportamento indefinito se mai
    un `RecordedStep` venisse costruito aggirando `__post_init__` (es. `dataclasses.replace` con
    un'azione non valida - `frozen=True` impedisce la mutazione diretta ma non un `replace`)."""


def replay_step(
    agent: ComputerAgent, adapter: UIAutomationAdapter, step: RecordedStep,
    timeout_seconds: float = 5.0, idempotency_key: str | None = None, parameters: dict[str, str] | None = None,
    policy_parameters: dict | None = None, automated: bool = False,
) -> ComputerActionResult:
    """Rigioca UN passo per davvero: trova la finestra (`find_window_by_title_containing`, F3.7 -
    stesso identico meccanismo di `SelectorEngine.locate`, F3.3.1) poi delega a `ComputerAgent.
    click_element`/`type_into_element` (F3.4.2) con `root=` la finestra appena trovata - MAI
    `window_title=` (quel percorso in `ComputerAgent` cerca per uguaglianza ESATTA del titolo,
    `find_window_by_title`, un contratto diverso e piu' fragile del `window_title_contains` per
    SOTTOSTRINGA gia' scelto per `RecordedStep`, F3.7). Una finestra non trovata si manifesta come
    `ComputerActionResult(success=False, error="WINDOW_NOT_FOUND")` - la STESSA identica forma
    d'errore che `click_element`/`type_into_element` gia' restituiscono per il proprio percorso
    `window_title`, cosi' un chiamante non deve gestire due forme diverse di "finestra non
    trovata" a seconda che l'azione venga da un replay o da una chiamata diretta.

    `parameters` (F3.8.2, adozione): se `step.text` contiene `${nome}` (`substitute_parameters`),
    sostituito PRIMA di scrivere - un placeholder senza valore corrispondente produce
    `ComputerActionResult(success=False, error="MISSING_PARAMETER")`, MAI il placeholder letterale
    scritto in un campo reale. Ignorato per `ACTION_CLICK` (un click non scrive nulla, `parameters`
    passato comunque da un chiamante che rigioca una lista mista di passi resta innocuo).

    `policy_parameters`/`automated` (F3.4.3, adozione): passati COSI' COME SONO a `click_element`/
    `type_into_element` insieme a `step.risk_intent` (`None` se il passo non ne ha dichiarato uno -
    comportamento invariato, F3.4.3 e' opt-in anche qui) - la verifica della policy resta intera
    responsabilita' di `ComputerAgent`/`agent.policy_engine`, questo modulo non duplica la
    decisione, solo inoltra il rischio gia' dichiarato in fase di registrazione."""
    from core.computer_use.ui_automation_adapter import WindowNotFoundError

    try:
        window = adapter.find_window_by_title_containing(step.selector.window_title_contains, timeout_seconds=timeout_seconds)
    except WindowNotFoundError:
        return ComputerActionResult(success=False, error="WINDOW_NOT_FOUND")

    if step.action == ACTION_CLICK:
        return agent.click_element(
            root=window, name=step.selector.name, control_type=step.selector.control_type,
            automation_id=step.selector.automation_id, timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key, risk_intent=step.risk_intent,
            policy_parameters=policy_parameters, automated=automated,
        )
    if step.action == ACTION_TYPE:
        try:
            resolved_text = substitute_parameters(step.text, parameters)
        except MissingParameterError:
            return ComputerActionResult(success=False, error="MISSING_PARAMETER")
        return agent.type_into_element(
            resolved_text, root=window, name=step.selector.name, control_type=step.selector.control_type,
            automation_id=step.selector.automation_id, timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key, risk_intent=step.risk_intent,
            policy_parameters=policy_parameters, automated=automated,
        )
    raise UnknownActionError(f"azione sconosciuta: {step.action!r}")


def replay_steps(
    agent: ComputerAgent, adapter: UIAutomationAdapter, steps: list[RecordedStep], timeout_seconds: float = 5.0,
    parameters: dict[str, str] | None = None, policy_parameters: dict | None = None, automated: bool = False,
) -> list[ComputerActionResult]:
    """Rigioca una LISTA di passi IN ORDINE, fermandosi al PRIMO fallimento (`success=False`) -
    coerente con lo spirito di F3.8.6 ("rilevare drift e sospendere la routine invece di
    improvvisare", non ancora costruito per intero: questo e' il comportamento minimo "non
    continuare alla cieca dopo un passo fallito", non una vera rilevazione di drift). I risultati
    dei passi GIA' eseguiti (compreso quello fallito) sono tutti restituiti, non solo un booleano
    finale - un chiamante deve poter vedere ESATTAMENTE dove si e' fermata la procedura.

    **Nessun `idempotency_key` (F3.4.6) generato automaticamente qui, deliberatamente**: una prima
    versione di questo metodo ne generava uno per indice di passo (`f"replay-step-{index}"`), ma
    la cache di `ComputerAgent` e' PER ISTANZA con una scadenza (30s di default, F3.4.6) - se la
    STESSA istanza `agent` rigiocasse due PROCEDURE DIVERSE (o la stessa procedura due volte
    apposta, es. su due finestre diverse) entro quella finestra, il passo 0 della seconda
    esecuzione avrebbe rischiato di collidere silenziosamente con la chiave del passo 0 della
    prima, restituendo un risultato CACHATO invece di eseguire per davvero - un buco reale trovato
    PRIMA di spedirlo, non ipotizzato, riflettendo su come la cache per-istanza gia' costruita in
    F3.4.6 interagirebbe con QUESTO nuovo chiamante. La protezione contro i retry resta comunque
    disponibile a chi la vuole: un chiamante puo' passare le proprie chiavi chiamando `replay_step`
    direttamente per ogni passo, con una chiave che SA essere univoca per la propria esecuzione
    (es. un id di corsa/procedura che F3.8.5, non ancora costruito, dovra' comunque generare)."""
    results: list[ComputerActionResult] = []
    for step in steps:
        result = replay_step(
            agent, adapter, step, timeout_seconds=timeout_seconds, parameters=parameters,
            policy_parameters=policy_parameters, automated=automated,
        )
        results.append(result)
        if not result.success:
            break
    return results


DRIFT_ERROR_CODES = frozenset({"WINDOW_NOT_FOUND", "NOT_FOUND", "AMBIGUOUS_MATCH"})


def is_likely_drift(result: ComputerActionResult) -> bool:
    """F3.8.6 (prima fetta - "rilevare drift"): `True` se il fallimento di `result` ha la FORMA di
    un cambiamento strutturale dell'app (il selettore che risolveva prima non risolve piu' a
    esattamente un elemento: finestra sparita, elemento sparito, o diventato ambiguo) - `False`
    per qualunque altro genere di fallimento (bloccato da policy, parametro mancante, l'elemento
    e' stato TROVATO ma l'azione e' fallita comunque a livello di esecuzione, `OPERATION_FAILED`)
    o per un successo. Nessuna euristica su QUANTE volte il selettore ha fallito ne' un confronto
    con un'esecuzione precedente - solo la FORMA di questo SINGOLO risultato, la stessa
    distinzione gia' esposta da `dry_run_step` tramite `DRY_RUN_NOT_FOUND`/`DRY_RUN_AMBIGUOUS`/
    `DRY_RUN_WINDOW_NOT_FOUND` per il percorso di verifica, qui applicata al risultato REALE di un
    replay (`ComputerActionResult`, non `DryRunStepResult`).

    Deliberatamente NON affrontata qui, la seconda meta' di F3.8.6 ("sospendere la routine invece
    di improvvisare"): questa funzione classifica un SINGOLO risultato, non decide ne' applica
    alcuna sospensione - un chiamante (es. una futura versione di `RunComputerProcedureSkill` che
    disabiliti/segnali una procedura dopo N drift consecutivi) resta libero di decidere cosa fare
    con questo segnale."""
    if result.success or result.error is None:
        return False
    return result.error in DRIFT_ERROR_CODES


DRY_RUN_WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
DRY_RUN_NOT_FOUND = "NOT_FOUND"
DRY_RUN_AMBIGUOUS = "AMBIGUOUS_MATCH"
DRY_RUN_MISSING_PARAMETER = "MISSING_PARAMETER"


@dataclass(frozen=True)
class DryRunStepResult:
    """F3.8.4 (prima fetta - "testarla in dry-run"): l'esito di UNA verifica dry-run, MAI un
    `ComputerActionResult` (che dichiara un'azione davvero eseguita - riusarlo qui rischierebbe
    di far credere a un chiamante distratto che un dry-run abbia click/scritto per davvero, la
    stessa distinzione gia' importante altrove in questo progetto tra "success" e "verified").
    `would_succeed=True` significa SOLO "il selettore risolve a esattamente un elemento adesso" -
    non garantisce che l'azione vera avrebbe successo (F3.5, la scala di ripiego, potrebbe ancora
    servire quando si esegue per davvero)."""

    step: RecordedStep
    would_succeed: bool
    error: str | None = None


def dry_run_step(
    adapter: UIAutomationAdapter, step: RecordedStep, timeout_seconds: float = 5.0,
    parameters: dict[str, str] | None = None, agent: ComputerAgent | None = None,
    policy_parameters: dict | None = None, automated: bool = False,
) -> DryRunStepResult:
    """F3.8.4 (prima fetta - "testarla in dry-run e su dati innocui"): verifica se `step.selector`
    risolverebbe DAVVERO a esattamente un elemento nello stato ATTUALE dell'app - senza mai
    cliccare/scrivere (nessuna azione DI COMPUTER USE vera qui, a differenza di `replay_step`).
    Riusa `SelectorEngine.locate()` (F3.3.1) per intero: la STESSA identica logica di risoluzione
    finestra+elemento del replay vero, non una sua reimplementazione parallela che potrebbe
    disallinearsi nel tempo (es. dichiarare "risolverebbe" con un criterio che il replay vero
    interpreta diversamente).

    `parameters` (F3.8.2, adozione): per un passo `ACTION_TYPE`, verifica ANCHE che
    `substitute_parameters(step.text, parameters)` non sollevi - un dry-run che dichiarasse
    "risolverebbe" ignorando un parametro mancante darebbe un falso senso di sicurezza, dato che
    il replay vero fallirebbe comunque con `MISSING_PARAMETER`.

    `agent`/`policy_parameters`/`automated` (F3.4.3, adozione): se `agent` e' dato (e collegato a
    un `policy_engine`) e `step.risk_intent` e' dato, riusa `ComputerAgent._check_policy` - la
    STESSA identica decisione che il replay vero prenderebbe, non una sua reimplementazione - per
    verificare ANCHE che la policy non fermerebbe il passo. Stesso principio di `parameters`
    sopra: un dry-run che ignorasse la policy darebbe un falso senso di sicurezza per un passo che
    il replay vero bloccherebbe con `POLICY_BLOCKED`/`CONFIRMATION_REQUIRED`/`AUTH_REQUIRED`.
    `agent=None` (default) salta questo controllo per intero - un dry-run senza un `ComputerAgent`
    a portata di mano non puo' verificare la policy, non deve fallire per un pre-requisito
    mancante (comportamento invariato).

    **"su dati innocui" (la seconda meta' di F3.8.4) NON e' affrontato qui**: questo dry-run non
    tocca MAI l'app (nemmeno leggere un valore) - "dati innocui" implicherebbe invece eseguire
    l'azione per davvero ma contro un ambiente/dato sicuro (es. una copia, un account di prova),
    un concetto diverso e piu' grande (richiede sapere COSA rende un dato "innocuo" per l'app
    target) non affrontato in questa prima fetta."""
    from core.computer_use.selector import AmbiguousSelectionError, NoMatchError, SelectorEngine
    from core.computer_use.ui_automation_adapter import WindowNotFoundError

    if agent is not None:
        policy_result = agent._check_policy(step.risk_intent, policy_parameters, automated)
        if policy_result is not None:
            return DryRunStepResult(step=step, would_succeed=False, error=policy_result.error)

    engine = SelectorEngine(adapter)
    try:
        engine.locate(step.selector, timeout_seconds=timeout_seconds)
    except WindowNotFoundError as exc:
        return DryRunStepResult(step=step, would_succeed=False, error=f"{DRY_RUN_WINDOW_NOT_FOUND}: {exc}")
    except NoMatchError as exc:
        return DryRunStepResult(step=step, would_succeed=False, error=f"{DRY_RUN_NOT_FOUND}: {exc}")
    except AmbiguousSelectionError as exc:
        return DryRunStepResult(step=step, would_succeed=False, error=f"{DRY_RUN_AMBIGUOUS}: {exc}")
    if step.action == ACTION_TYPE:
        try:
            substitute_parameters(step.text, parameters)
        except MissingParameterError as exc:
            return DryRunStepResult(step=step, would_succeed=False, error=f"{DRY_RUN_MISSING_PARAMETER}: {exc}")
    return DryRunStepResult(step=step, would_succeed=True)


def dry_run_steps(
    adapter: UIAutomationAdapter, steps: list[RecordedStep], timeout_seconds: float = 5.0,
    parameters: dict[str, str] | None = None, agent: ComputerAgent | None = None,
    policy_parameters: dict | None = None, automated: bool = False,
) -> list[DryRunStepResult]:
    """A differenza di `replay_steps` (che si ferma al PRIMO fallimento, perche' un'azione vera
    puo' dipendere dallo stato lasciato dalla precedente), un dry-run verifica OGNI passo fino in
    fondo anche dopo un `would_succeed=False` - nessuna azione viene mai eseguita, quindi non
    esiste uno stato che un passo "rompe" per i successivi: un chiamante vuole vedere TUTTI i
    passi che non risolverebbero oggi, non fermarsi al primo per poi dover rilanciare piu' volte
    per scoprire gli altri."""
    return [
        dry_run_step(
            adapter, step, timeout_seconds=timeout_seconds, parameters=parameters, agent=agent,
            policy_parameters=policy_parameters, automated=automated,
        )
        for step in steps
    ]
