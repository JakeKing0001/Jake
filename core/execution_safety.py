"""Rete di sicurezza condivisa per l'esecuzione di un passo (v3.3, fase Agent Engine 2.0).

Prima questi tre controlli - ritenta gli errori transitori, verifica in modo indipendente che
l'effetto dichiarato sia avvenuto davvero, annulla un'operazione filesystem se serve - vivevano
solo dentro PlanExecutor (core/plan_executor.py), il vecchio esecutore a piano fisso. L'agente a
passi (core/agent.py, TaskAgent), che oggi e' il percorso piu' usato per le richieste composte
("e poi", "e aprilo"), eseguiva le stesse identiche skill (create/rename/move/delete un file...)
senza nessuno dei tre: un OPERATION_FAILED transitorio bruciava un passo di ragionamento invece
di essere ritentato, e un errore a meta' compito lasciava sul disco gli effetti collaterali gia'
fatti senza nessun tentativo di annullarli. Estratta qui cosi' i due esecutori condividono la
stessa logica invece di poterla far divergere in silenzio, come sarebbe successo copiandola."""
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from core.action_ledger import ActionReceipt, authorization_of, idempotency_key_of, new_action_id
from core.command import Command
from core.identity import current_windows_user
from core.logger import log_action
from core.policy_engine import POLICY_REASONS
from core.request_context import current_device_id
from core.risk import RiskLevel, risk_of
from core.skill_result import SkillResult

RETRYABLE_ERRORS = {"OPERATION_FAILED", "NETWORK_UNAVAILABLE"}
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ActionExecution:
    """Esito per-azione: bersaglio effettivo e motivazione fuori dai dati della skill (F1.2.6).

    None significa che nessuna decisione di policy e' disponibile, non ALLOW implicito.
    Non e' un token di autorizzazione e non rende sicuro il dispatcher grezzo.
    """

    command: Command
    result: SkillResult | None
    note: str | None = None
    policy_reason: str | None = None

    def __post_init__(self) -> None:
        if self.policy_reason is not None and (
            not isinstance(self.policy_reason, str) or self.policy_reason not in POLICY_REASONS
        ):
            raise ValueError("ActionExecution.policy_reason non valida")


def is_safe_to_auto_retry(intent: str) -> bool:
    """F1.3.6 ("impedire retry automatico per azioni non idempotenti senza chiave deduplica"):
    execute_with_retry() ritentava CIECAMENTE qualunque intent con un errore transitorio, senza
    sapere se ripetere la skill potesse produrre un EFFETTO DOPPIO - verificato che non e' un
    rischio teorico: ~37 skill (tra cui skills/notes.py::ADD_NOTE, skills/contacts.py,
    skills/git_control.py) possono davvero restituire OPERATION_FAILED, e un secondo tentativo
    dopo che la prima scrittura era gia' andata a buon fine per un'altra ragione aggiungerebbe lo
    stesso appunto/contatto due volte. Un intent e' considerato sicuro da ritentare
    automaticamente solo se: (a) e' READ_ONLY (nessun effetto collaterale da poter raddoppiare),
    oppure (b) e' uno dei quattro intent filesystem in INTENT_SAFETY_REGISTRY (definito piu'
    sotto in questo stesso modulo) - naturalmente idempotenti per costruzione: ricreare/ri-
    cancellare/ri-rinominare/ri-spostare lo stesso percorso raggiunge lo stesso stato finale o
    fallisce in modo pulito (es. PATH_NOT_FOUND), mai un doppio effetto. Per tutti gli altri
    intent, un errore transitorio non viene piu' ritentato automaticamente - piu' sicuro
    ("minimo privilegio"/"negare per default", vedi ROADMAP_EXECUTION.md F1.2.4) che rischiare un
    effetto doppio su una skill mai controllata caso per caso. Una vera enforcement con chiave di
    idempotenza (gia' tracciata da idempotency_key_of in core/action_ledger.py, non ancora
    applicata) resta lavoro futuro dichiarato, non questo."""
    return risk_of(intent) == RiskLevel.READ_ONLY or intent in INTENT_SAFETY_REGISTRY


def execute_with_retry(execute_fn, intent: str, parameters: dict) -> tuple[SkillResult, int]:
    """Chiama execute_fn(intent, parameters), ritentando fino a MAX_ATTEMPTS volte se l'errore e'
    tra quelli transitori (RETRYABLE_ERRORS) E l'intent e' sicuro da ritentare automaticamente
    (vedi is_safe_to_auto_retry, F1.3.6) - per un intent non idempotente il primo errore
    transitorio e' gia' definitivo, per non rischiare un effetto doppio. execute_fn e' un
    callable qualsiasi (SkillRegistry.execute, o il ripiego piu' ricco di JakeCore.
    _resolve_and_execute): questa funzione non sa e non le importa cosa faccia davvero, decide
    solo in base al risultato e all'intent. Restituisce (risultato, tentativi fatti)."""
    execution, attempts = execute_action_with_retry(execute_fn, intent, parameters)
    assert execution.result is not None  # normalizzato da execute_action_with_retry
    return execution.result, attempts


def execute_action_with_retry(
    execute_fn: Callable[[str, dict], SkillResult | ActionExecution | None], intent: str, parameters: dict,
) -> tuple[ActionExecution, int]:
    """Stessa rete di retry, conservando il contesto dell'ULTIMO tentativo (F1.2.6).

    Un executor legacy restituisce SkillResult: nessuna motivazione viene inventata.
    Se il core riscrive l'intent, si conserva il bersaglio vero anche per audit/verifica.
    """
    attempts = 0
    execution = ActionExecution(Command(intent, parameters), None)
    max_attempts = MAX_ATTEMPTS if is_safe_to_auto_retry(intent) else 1
    while attempts < max_attempts:
        attempts += 1
        returned = execute_fn(intent, parameters)
        execution = returned if isinstance(returned, ActionExecution) else ActionExecution(Command(intent, parameters), returned)
        result = execution.result
        if result is None:
            execution = replace(execution, result=SkillResult(success=False, data={}, error="UNKNOWN_INTENT"))
            break
        if result.success or result.error not in RETRYABLE_ERRORS or not is_safe_to_auto_retry(execution.command.intent):
            break
    # max_attempts >= 1 garantisce che il ciclo giri almeno una volta, quindi result non e' mai
    # None qui davvero - ma un ripiego esplicito (invece di fidarsi solo di quell'invariante)
    # evita che un futuro MAX_ATTEMPTS = 0 restituisca None a un chiamante che si aspetta sempre
    # un vero SkillResult.
    if execution.result is None:
        execution = replace(execution, result=SkillResult(success=False, data={}, error="UNKNOWN_INTENT"))
    return execution, attempts


def _rollback_create_path(registry, data, policy_engine):
    registry.execute("DELETE_PATH", {"path": data["path"], "confirmed": True}, policy_engine=policy_engine)


def _rollback_rename_path(registry, data, policy_engine):
    original_name = Path(data["path"]).name
    registry.execute(
        "RENAME_PATH", {"path": data["new_path"], "new_name": original_name}, policy_engine=policy_engine,
    )


def _rollback_move_path(registry, data, policy_engine):
    original_dir = str(Path(data["path"]).parent)
    registry.execute(
        "MOVE_PATH", {"path": data["new_path"], "destination": original_dir, "confirmed": True},
        policy_engine=policy_engine,
    )


def _verify_process_terminated(data: dict) -> bool:
    """F1.3.2 ("prove forti per... processi"): controlla per davvero che il pid non esista
    piu', invece di fidarsi del successo dichiarato da KillProcessByPortSkill (che dalla
    correzione in skills/dev_tools.py attende gia' lei stessa la morte del processo prima di
    dichiarare successo - questo e' un secondo controllo indipendente, non l'unico)."""
    import psutil

    return not psutil.pid_exists(data["pid"])


def _verify_window_closed(data: dict) -> bool:
    """F1.3.2 ("prove forti per... finestre"): controlla per davvero che l'handle non sia piu'
    una finestra valida, invece di fidarsi del successo dichiarato da CloseWindowSkill (che dalla
    correzione in skills/close_window.py attende gia' lei stessa la chiusura reale prima di
    dichiarare successo - questo e' un secondo controllo indipendente, non l'unico, stesso
    principio di _verify_process_terminated sopra)."""
    import win32gui

    return not win32gui.IsWindow(data["hwnd"])


@dataclass(frozen=True)
class RollbackAction:
    """L'inverso naturale di un intent gia' eseguito con successo, e l'intent che DAVVERO esegue
    (i tre handler sotto chiamano registry.execute() con "confirmed": True gia' impostato, per
    compensare un effetto gia' approvato senza bloccarsi in attesa di una conferma che qui
    nessuno puo' dare - vedi rollback_effect). compensating_intent serve a controllare
    blocked_intents PRIMA di chiamare handler, non dopo (F1.2.5). handler riceve anche
    policy_engine (F1.2.1, percorso 7): SkillRegistry.execute() e' ora fail-closed di default,
    quindi va rifornito qui dello stesso policy_engine gia' controllato sopra - non e' un secondo
    controllo diverso, e' lo stesso risultato gia' deciso, solo passato al dispatcher grezzo che
    lo richiede."""

    handler: Callable[[object, dict, object], None]
    compensating_intent: str


@dataclass(frozen=True)
class IntentSafetyEntry:
    """F1.3.1: un registry unico intent -> verifier -> compensation, invece delle tre strutture
    parallele che c'erano prima (VERIFIABLE_INTENTS, l'if/elif di verify_effect, ROLLBACK_HANDLERS
    + ROLLBACK_COMPENSATING_INTENT) che dovevano restare sincronizzate a mano: esattamente il
    pattern di bug gia' documentato altrove in questo progetto (vedi core/policy_engine.py sul
    bug di RunWorkflowSkill nato da due insiemi paralleli scollegati). verifier e' None per un
    intent senza controllo indipendente possibile; rollback e' None per un intent senza inverso
    naturale (es. DELETE_PATH: cancellare non si annulla)."""

    verifier: Optional[Callable[[dict], bool]] = None
    rollback: Optional[RollbackAction] = None


# Le operazioni filesystem hanno un effetto verificabile senza dipendenze aggiuntive E un
# inverso naturale (per lo schermo/UI arrivera' con il Computer Use Engine, fase 3.7).
# KILL_PROCESS_BY_PORT/CLOSE_WINDOW (F1.3.2) hanno solo il primo: un processo terminato o una
# finestra chiusa non hanno un "rollback" sensato (non si puo' far ripartire lo stato esatto di
# prima). Le altre azioni (aprire un'app, cercare, ricordare un'informazione, ...) non hanno
# ne' l'uno ne' l'altro e restano fuori da questo registry (verify_effect/rollback_effect le
# trattano di conseguenza).
INTENT_SAFETY_REGISTRY: dict[str, IntentSafetyEntry] = {
    "CREATE_PATH": IntentSafetyEntry(
        verifier=lambda data: Path(data["path"]).exists(),
        rollback=RollbackAction(_rollback_create_path, "DELETE_PATH"),
    ),
    "RENAME_PATH": IntentSafetyEntry(
        verifier=lambda data: Path(data["new_path"]).exists(),
        rollback=RollbackAction(_rollback_rename_path, "RENAME_PATH"),
    ),
    "MOVE_PATH": IntentSafetyEntry(
        verifier=lambda data: Path(data["new_path"]).exists(),
        rollback=RollbackAction(_rollback_move_path, "MOVE_PATH"),
    ),
    "KILL_PROCESS_BY_PORT": IntentSafetyEntry(
        verifier=_verify_process_terminated,
        rollback=None,  # terminare un processo non ha un inverso naturale
    ),
    "CLOSE_WINDOW": IntentSafetyEntry(
        verifier=_verify_window_closed,
        rollback=None,  # non si puo' "riaprire" una finestra nello stato esatto di prima
    ),
    "DELETE_PATH": IntentSafetyEntry(
        verifier=lambda data: not Path(data["path"]).exists(),
        rollback=None,  # cancellare non ha un inverso naturale
    ),
}

# Derivato dal registry, non piu' mantenuto a mano: non puo' andare fuori sincrono con
# verify_effect, perche' e' la STESSA fonte che verify_effect consulta (vedi sotto). Usato dai due
# esecutori (core/agent.py, core/plan_executor.py) per decidere quando il log strutturato (F0,
# core/logger.log_action) puo' scrivere verified=True/False invece di lasciarlo assente -
# verify_effect restituisce True anche quando non ha nessuna verifica da fare, e riportarlo come
# verified=True affermerebbe una prova mai avvenuta per ogni altro intent.
VERIFIABLE_INTENTS = frozenset(
    intent for intent, entry in INTENT_SAFETY_REGISTRY.items() if entry.verifier is not None
)


def verify_effect(intent: str, data: dict) -> bool:
    """Controlla in modo indipendente che l'effetto dichiarato da una skill sia davvero
    avvenuto (v1.5), invece di fidarsi ciecamente del 'success' riportato - vedi
    INTENT_SAFETY_REGISTRY per quali intent hanno un verificatore."""
    entry = INTENT_SAFETY_REGISTRY.get(intent)
    if entry is None or entry.verifier is None:
        return True  # nessuna verifica indipendente disponibile per questo intent
    return entry.verifier(data)


def rollback_effect(
    registry, intent: str, data: dict, policy_engine=None, *,
    action_ledger=None, trace_id: str | None = None, requested_by: str | None = None, private: bool = False,
) -> bool:
    """Annulla l'effetto di un passo gia' eseguito con successo, se esiste un inverso noto per
    il suo intent (vedi INTENT_SAFETY_REGISTRY). Vero se e' stato davvero annullato; gli errori
    nel rollback stesso vengono inghiottiti (un rollback fallito non deve mai far crashare il
    chiamante, ne' mascherare l'errore originale che ha scatenato il rollback).

    F1.7.2 ("collegare command, sub-step, verifica, undo e notifica con lo stesso trace id"):
    buco reale - un rollback non produceva MAI una propria `ActionReceipt`, quindi il ledger non
    mostrava da nessuna parte che un'azione era stata annullata (solo la ricevuta dell'azione
    ORIGINALE restava, con "success", indistinguibile da un'azione mai annullata). `action_ledger`/
    `trace_id` sono opzionali per compatibilita' con chi non ne ha ancora uno da passare (nessun
    cambio di comportamento se omessi, stesso principio "opt-in" gia' usato altrove in F1) - se
    presenti, un rollback davvero TENTATO (l'intent ha un inverso noto E la policy lo permette)
    scrive una ricevuta con lo STESSO `trace_id` dell'azione originale che l'ha innescato, cosi'
    un audit del ledger correla i due eventi invece di vederli come scollegati. `intent` nella
    ricevuta e' l'intent COMPENSATORIO che ha eseguito per davvero (es. `DELETE_PATH` per
    annullare un `CREATE_PATH`), non l'intent originale - e' quello che e' successo sul serio.
    `requested_by` porta il prefisso `"rollback:"` (es. `"rollback:agent:general"`) per
    distinguere nel ledger un'azione eseguita come conseguenza automatica di un rollback da
    un'azione richiesta direttamente con lo stesso intent - un valore costruito da un template
    fisso più `self.agent_name`/`requested_by` gia' esistenti (mai testo libero derivato
    dall'utente), stesso principio di sicurezza gia' applicato a `policy_reason`. Un rollback non
    tentato (nessun inverso noto, o bloccato da `blocked_intents`) resta senza ricevuta, come
    prima: quel caso non e' un'esecuzione, non c'e' nulla di nuovo da correlare.

    F1.7.6 ("mostrare... rollback rate"): un rollback tentato scrive ora anche in
    `data/jake_actions.jsonl` (`core/logger.log_action`, letto da `tools/dashboard.py`), stesso
    schema gia' seguito dagli altri quattro chokepoint. `result` e' sempre `"rollback_success"`/
    `"rollback_failed"` (mai il generico `"success"` di un'azione qualsiasi): la dashboard puo'
    cosi' calcolare un vero "rollback rate" contando i risultati con prefisso `"rollback_"`,
    invece di doverli confondere con un'esecuzione normale dello stesso intent.

    F1.2.5: policy_engine (core/policy_engine.py::PolicyEngine) e' opzionale per compatibilita'
    con i chiamanti che non ne hanno ancora uno da passare, ma quando c'e' un blocked_intents
    che include l'intent compensatorio, il rollback NON parte: prima di questa correzione i tre
    handler chiamavano registry.execute() direttamente, con "confirmed": True auto-iniettato,
    bypassando PolicyEngine del tutto - un DELETE_PATH disabilitato dall'utente restava comunque
    eseguibile come "annullamento" di un CREATE_PATH. Non si passa invece da decide_automated():
    quello richiederebbe CONFIRM per un intent DESTRUCTIVE/ADMIN, ma qui nessun utente e' pronto
    a confermare in tempo reale - bloccare resta l'unico controllo che ha senso applicare a
    un'azione gia' approvata in origine.

    F1.2.1 (percorso 6): `policy_engine=None` e' FAIL-CLOSED (nessun rollback), non piu' "nessun
    controllo" - stesso principio gia' applicato a `PlanExecutor.execute()` (F1.2.1, percorso 3).
    In produzione `JakeCore.__init__` collega gia' `policy_engine` a tutti e tre i `TaskAgent`
    (F1.2.5, `self.agent.policy_engine = self.policy_engine` e i suoi due gemelli, DOPO la
    creazione perche' `PolicyEngine` non esiste ancora quando i tre `TaskAgent` vengono costruiti)
    - questo non era quindi un buco gia' sfruttabile in produzione. E' pero' lo stesso principio
    "nega per default" gia' applicato a `PlanExecutor.execute()`: un `TaskAgent` costruito senza
    collegare `policy_engine` esplicitamente (un test, uno strumento, un futuro chiamante) non
    deve poter eseguire un rollback senza NESSUN controllo su `blocked_intents` solo perche' se
    l'e' dimenticato - lo stesso ragionamento che ha reso `PlanExecutor.execute()` fail-closed.

    F1.2.1 (percorso 7): ora che anche `SkillRegistry.execute()` e' fail-closed di default (vedi
    il suo docstring), `policy_engine` viene passato anche a `entry.rollback.handler(...)` - non
    un secondo controllo diverso, e' lo stesso `policy_engine` gia' verificato qui sopra contro
    `blocked_intents`, solo rifornito al dispatcher grezzo che lo richiede per non bloccarsi da
    solo su un rollback gia' approvato."""
    entry = INTENT_SAFETY_REGISTRY.get(intent)
    if entry is None or entry.rollback is None:
        return False
    compensating_intent = entry.rollback.compensating_intent
    if policy_engine is None or compensating_intent in policy_engine.blocked_intents:
        return False
    try:
        entry.rollback.handler(registry, data, policy_engine)
        succeeded = True
    except Exception:
        succeeded = False
    if action_ledger is not None and trace_id is not None:
        result = "rollback_success" if succeeded else "rollback_failed"
        risk_decision = risk_of(compensating_intent).value
        # F1.7.6 ("mostrare... rollback rate"): stesso schema gia' seguito dai quattro chokepoint
        # esistenti (JakeCore/TaskAgent/PlanExecutor) - un rollback scrive ora anche in
        # data/jake_actions.jsonl (core/logger.log_action, letto da tools/dashboard.py), non solo
        # nel ledger append-only. `result` inizia sempre con "rollback_" (mai il generico
        # "success" usato per un'azione normale): la dashboard puo' cosi' distinguere un vero
        # rollback da un'esecuzione qualsiasi con lo stesso intent, invece di doverli confondere.
        log_action(
            trace_id, private=private, model=None, skill=compensating_intent,
            risk_decision=risk_decision, result=result,
        )
        action_ledger.record(
            ActionReceipt(
                action_id=new_action_id(), trace_id=trace_id, ts=time.time(), intent=compensating_intent,
                requested_by=f"rollback:{requested_by}" if requested_by else "rollback",
                risk_decision=risk_decision,
                authorization=authorization_of(result, None), result=result,
                idempotency_key=idempotency_key_of(compensating_intent, data),
                device_id=current_device_id(), windows_user=current_windows_user(),
            ),
            private=private,
        )
    return succeeded
