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
- F3.8.2 (inferire parametri variabili e precondizioni - qui `text` e' sempre un valore LETTERALE
  gia' registrato, mai un parametro da sostituire a runtime);
- F3.8.3 (mostrare la procedura generalizzata all'utente - nessuna UI/HUD qui);
- F3.8.4 (prima fetta - "dry-run" - CHIUSA in un incremento successivo, 19/09/2026):
  `dry_run_step()`/`dry_run_steps()` - vedi le loro docstring. Resta aperta la seconda meta'
  ("su dati innocui" - eseguire per davvero ma contro un ambiente/dato sicuro, diverso da "non
  eseguire affatto");
- F3.8.5 (salvare versione/app target/selector/undo - nessuna persistenza a lungo termine, solo
  la forma serializzabile di un singolo passo/lista di passi in memoria);
- F3.8.6 (rilevare drift e sospendersi - `replay_steps` si ferma al primo fallimento, F3.5, ma non
  distingue "l'app e' cambiata struttura" da un qualunque altro fallimento transitorio);
- F3.8.7 (richiedere nuova approvazione se capability/impatto cambiano - nessun collegamento a
  `core/policy_engine.py` qui, gia' escluso esplicitamente anche da F3.4.3)."""
from dataclasses import dataclass

from core.computer_agent import ComputerActionResult, ComputerAgent
from core.computer_use.selector import ElementSelector
from core.computer_use.ui_automation_adapter import UIAutomationAdapter

ACTION_CLICK = "click"
ACTION_TYPE = "type"
_KNOWN_ACTIONS = frozenset({ACTION_CLICK, ACTION_TYPE})


@dataclass(frozen=True)
class RecordedStep:
    """Un passo REGISTRATO: QUALE azione (`action`), SU QUALE elemento (`selector`, mai una
    coordinata), CON QUALE testo (`text`, solo per `ACTION_TYPE` - `None` per `ACTION_CLICK`,
    un valore letterale gia' deciso in fase di registrazione per `ACTION_TYPE`, F3.8.2 non ancora
    affrontato). `selector.window_title_contains` e' OBBLIGATORIO qui (a differenza di
    `ElementSelector` da sola, dove resta opzionale) - un passo REGISTRATO deve poter essere
    rigiocato in una sessione futura senza alcuna finestra gia' risolta a portata di mano, lo
    stesso motivo che ha gia' portato a costruire quel campo in F3.3.1."""

    action: str
    selector: ElementSelector
    text: str | None = None

    def __post_init__(self) -> None:
        if self.action not in _KNOWN_ACTIONS:
            raise ValueError(f"RecordedStep.action sconosciuta: {self.action!r} (attese: {sorted(_KNOWN_ACTIONS)})")
        if self.selector.window_title_contains is None:
            raise ValueError("RecordedStep richiede selector.window_title_contains (vedi il docstring della classe)")
        if self.action == ACTION_TYPE and self.text is None:
            raise ValueError("RecordedStep con action='type' richiede 'text'")
        if self.action == ACTION_CLICK and self.text is not None:
            raise ValueError("RecordedStep con action='click' non deve avere 'text' (un click non scrive nulla)")

    def to_dict(self) -> dict:
        """Come `ElementSelector.to_dict` (F3.3.4): un dict semplice, agnostico rispetto a DOVE/
        COME un chiamante lo persiste davvero (F3.8.5, non affrontato qui)."""
        result: dict = {"action": self.action, "selector": self.selector.to_dict()}
        if self.text is not None:
            result["text"] = self.text
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "RecordedStep":
        """L'inverso di `to_dict` - stesso principio "rifiuta invece di indovinare" gia' seguito
        da `ElementSelector.from_dict`: una chiave sconosciuta solleva `ValueError` invece di
        essere ignorata silenziosamente."""
        unknown_keys = set(data) - {"action", "selector", "text"}
        if unknown_keys:
            raise ValueError(f"RecordedStep.from_dict: chiavi sconosciute {sorted(unknown_keys)}")
        if "action" not in data or "selector" not in data:
            raise ValueError("RecordedStep.from_dict richiede almeno 'action' e 'selector'")
        return cls(
            action=data["action"], selector=ElementSelector.from_dict(data["selector"]),
            text=data.get("text"),
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
    timeout_seconds: float = 5.0, idempotency_key: str | None = None,
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
    trovata" a seconda che l'azione venga da un replay o da una chiamata diretta."""
    from core.computer_use.ui_automation_adapter import WindowNotFoundError

    try:
        window = adapter.find_window_by_title_containing(step.selector.window_title_contains, timeout_seconds=timeout_seconds)
    except WindowNotFoundError:
        return ComputerActionResult(success=False, error="WINDOW_NOT_FOUND")

    if step.action == ACTION_CLICK:
        return agent.click_element(
            root=window, name=step.selector.name, control_type=step.selector.control_type,
            automation_id=step.selector.automation_id, timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )
    if step.action == ACTION_TYPE:
        return agent.type_into_element(
            step.text, root=window, name=step.selector.name, control_type=step.selector.control_type,
            automation_id=step.selector.automation_id, timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )
    raise UnknownActionError(f"azione sconosciuta: {step.action!r}")


def replay_steps(
    agent: ComputerAgent, adapter: UIAutomationAdapter, steps: list[RecordedStep], timeout_seconds: float = 5.0,
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
        result = replay_step(agent, adapter, step, timeout_seconds=timeout_seconds)
        results.append(result)
        if not result.success:
            break
    return results


DRY_RUN_WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
DRY_RUN_NOT_FOUND = "NOT_FOUND"
DRY_RUN_AMBIGUOUS = "AMBIGUOUS_MATCH"


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


def dry_run_step(adapter: UIAutomationAdapter, step: RecordedStep, timeout_seconds: float = 5.0) -> DryRunStepResult:
    """F3.8.4 (prima fetta - "testarla in dry-run e su dati innocui"): verifica se `step.selector`
    risolverebbe DAVVERO a esattamente un elemento nello stato ATTUALE dell'app - senza mai
    cliccare/scrivere (nessun `ComputerAgent` coinvolto qui, a differenza di `replay_step`). Riusa
    `SelectorEngine.locate()` (F3.3.1) per intero: la STESSA identica logica di risoluzione
    finestra+elemento del replay vero, non una sua reimplementazione parallela che potrebbe
    disallinearsi nel tempo (es. dichiarare "risolverebbe" con un criterio che il replay vero
    interpreta diversamente).

    **"su dati innocui" (la seconda meta' di F3.8.4) NON e' affrontato qui**: questo dry-run non
    tocca MAI l'app (nemmeno leggere un valore) - "dati innocui" implicherebbe invece eseguire
    l'azione per davvero ma contro un ambiente/dato sicuro (es. una copia, un account di prova),
    un concetto diverso e piu' grande (richiede sapere COSA rende un dato "innocuo" per l'app
    target) non affrontato in questa prima fetta."""
    from core.computer_use.selector import AmbiguousSelectionError, NoMatchError, SelectorEngine
    from core.computer_use.ui_automation_adapter import WindowNotFoundError

    engine = SelectorEngine(adapter)
    try:
        engine.locate(step.selector, timeout_seconds=timeout_seconds)
    except WindowNotFoundError as exc:
        return DryRunStepResult(step=step, would_succeed=False, error=f"{DRY_RUN_WINDOW_NOT_FOUND}: {exc}")
    except NoMatchError as exc:
        return DryRunStepResult(step=step, would_succeed=False, error=f"{DRY_RUN_NOT_FOUND}: {exc}")
    except AmbiguousSelectionError as exc:
        return DryRunStepResult(step=step, would_succeed=False, error=f"{DRY_RUN_AMBIGUOUS}: {exc}")
    return DryRunStepResult(step=step, would_succeed=True)


def dry_run_steps(
    adapter: UIAutomationAdapter, steps: list[RecordedStep], timeout_seconds: float = 5.0,
) -> list[DryRunStepResult]:
    """A differenza di `replay_steps` (che si ferma al PRIMO fallimento, perche' un'azione vera
    puo' dipendere dallo stato lasciato dalla precedente), un dry-run verifica OGNI passo fino in
    fondo anche dopo un `would_succeed=False` - nessuna azione viene mai eseguita, quindi non
    esiste uno stato che un passo "rompe" per i successivi: un chiamante vuole vedere TUTTI i
    passi che non risolverebbero oggi, non fermarsi al primo per poi dover rilanciare piu' volte
    per scoprire gli altri."""
    return [dry_run_step(adapter, step, timeout_seconds=timeout_seconds) for step in steps]
