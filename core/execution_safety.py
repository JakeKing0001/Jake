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
import ctypes
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
from core.turn_cancellation import TurnCancelled, cancellation_suspended, current_turn_cancelled

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
    oppure (b) e' in INTENT_SAFETY_REGISTRY (definito piu' sotto in questo stesso modulo) -
    naturalmente idempotente per costruzione: ricreare/ri-cancellare/ri-rinominare/ri-spostare lo
    stesso percorso, o ri-estrarre lo stesso archivio sulla stessa destinazione (EXTRACT_ARCHIVE,
    F1 Gate G1), raggiunge lo stesso stato finale o fallisce in modo pulito (es. PATH_NOT_FOUND),
    mai un doppio effetto. Le due voci CREATE_SKILL/DELETE_CREATED_SKILL aggiunte insieme a
    EXTRACT_ARCHIVE non cambiano nulla QUI in pratica - nessuno dei loro codici di errore
    (MISSING_PARAMETERS/FORGE_FAILED/CONFIRMATION_REQUIRED/NOT_FOUND) e' in RETRYABLE_ERRORS,
    quindi non diventano mai davvero ritentate - ma restano comunque nel registro per il loro
    verificatore indipendente, non per l'idempotenza a retry (mai verificata per install()/
    delete() della fucina, che tocca anche il registro delle skill, non solo il filesystem).
    RESTART_EXPLORER (aggiunta successiva, stesso Gate G1) e' invece idempotente allo stesso modo
    dei quattro intent filesystem: ri-terminare e riavviare Explorer un'altra volta raggiunge lo
    stesso stato finale (Explorer in esecuzione), mai un doppio effetto dannoso. Stesso discorso
    per EMPTY_RECYCLE_BIN: EmptyRecycleBinSkill tratta gia' il codice "cestino gia' vuoto" come
    successo (skills/recycle_bin.py), quindi ri-svuotarlo raggiunge sempre lo stesso stato finale.
    Per
    tutti gli altri intent, un errore transitorio non viene piu' ritentato automaticamente - piu'
    sicuro ("minimo privilegio"/"negare per default", vedi ROADMAP_EXECUTION.md F1.2.4) che
    rischiare un effetto doppio su una skill mai controllata caso per caso. Una vera enforcement
    con chiave di idempotenza (gia' tracciata da idempotency_key_of in core/action_ledger.py, non
    ancora applicata) resta lavoro futuro dichiarato, non questo."""
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
        if attempts and current_turn_cancelled():
            break  # "Jake, basta" tra un tentativo e l'altro: nessun nuovo tentativo
        attempts += 1
        try:
            returned = execute_fn(intent, parameters)
        except TurnCancelled:
            # Annullato DENTRO la skill (attesa del modello, click bloccato): esito CANCELLED, cosi'
            # agente e piano fermano il compito e compensano i passi gia' fatti come per un arresto
            # tra un passo e l'altro.
            execution = replace(execution, result=SkillResult(success=False, data={}, error="CANCELLED"))
            break
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


# F1.3.5 ("generare undo token con scadenza e precondizioni"): le quattro funzioni
# "_*_undo_params" sotto sono ESTRATTE dai rispettivi handler di rollback (stesso identico
# calcolo di prima, nessun cambio di comportamento) cosi' da poterle riusare anche fuori dal
# percorso di compensazione automatica - vedi generate_undo_descriptor() piu' sotto, che le
# chiama per popolare un vero UndoDescriptor.compensating_parameters invece di duplicare a mano
# lo stesso calcolo una seconda volta (esattamente il pattern "due insiemi paralleli scollegati"
# che il docstring di IntentSafetyEntry mette gia' in guardia altrove in questo modulo).
def _create_path_undo_params(data: dict) -> dict:
    return {"path": data["path"], "confirmed": True}


def _rollback_create_path(registry, data, policy_engine):
    registry.execute("DELETE_PATH", _create_path_undo_params(data), policy_engine=policy_engine)


def _rename_path_undo_params(data: dict) -> dict:
    return {"path": data["new_path"], "new_name": Path(data["path"]).name}


def _rollback_rename_path(registry, data, policy_engine):
    registry.execute("RENAME_PATH", _rename_path_undo_params(data), policy_engine=policy_engine)


def _move_path_undo_params(data: dict) -> dict:
    return {"path": data["new_path"], "destination": str(Path(data["path"]).parent), "confirmed": True}


def _rollback_move_path(registry, data, policy_engine):
    registry.execute("MOVE_PATH", _move_path_undo_params(data), policy_engine=policy_engine)


def _extract_archive_undo_params(data: dict) -> dict:
    return {"path": data["destination"], "confirmed": True}


def _rollback_extract_archive(registry, data, policy_engine):
    registry.execute("DELETE_PATH", _extract_archive_undo_params(data), policy_engine=policy_engine)


# F1.3 (criterio di uscita, "80% delle azioni reversibili dispone di undo testato" - vedi
# ROADMAP_EXECUTION.md): dieci coppie in piu' oltre alle quattro sopra, trovate riesaminando UNA
# PER UNA tutte le 67 intent classificate LOCAL_REVERSIBLE in core/risk.py (non ipotizzate dal
# nome) e verificando il codice VERO di ciascuna skill candidata prima di collegarla, con un
# criterio di esclusione unico e applicato con coerenza: un'azione entra qui SOLO se (a) esiste
# gia' una skill che esegue davvero il suo inverso naturale, (b) quella skill e' raggiungibile
# con SOLI dati gia' presenti nel risultato riuscito dell'azione originale (mai uno stato "prima"
# da catturare - quello e' F1.3.4, un problema diverso), e (c) l'operazione originale e' un
# INSERT puro (una nuova riga/voce sempre distinta), MAI un upsert - un upsert su una chiave che
# potrebbe gia' esistere renderebbe l'undo pericoloso: cancellerebbe una voce PRECEDENTE
# all'azione da annullare, non solo quella appena creata. Scartate per questo motivo, verificato
# leggendo il codice (non assunto dal nome): REMEMBER (core/memory_manager.py::remember() e' un
# upsert su key+category), SET_TRIGGER (trigger_manager.save() chiama lo STESSO remember() upsert
# sopra), LEARN_COMMAND (core/nlu/examples.py::add_learned() rimuove esplicitamente qualunque
# esempio con la stessa chiave prima di aggiungere il nuovo), COMPRESS_PATH (shutil.make_archive
# sovrascrive in silenzio un archivio .zip che avesse gia' quel nome) ed EXPORT_NOTES
# (Path.write_text sovrascrive incondizionatamente qualunque file preesistente a quel percorso,
# nessun controllo di esistenza prima di scrivere). SET_WINDOW_ALWAYS_ON_TOP resta fuori perche'
# non esiste alcuna skill che tolga il flag "sempre in primo piano" (nessun inverso possibile
# oggi); SNAP_WINDOW_LEFT/RIGHT restano fuori perche' non restituiscono alcun dato che identifichi
# la finestra spostata (data={}), quindi RESTORE_WINDOW non avrebbe un bersaglio.
#
# SET_PRIVATE_MODE ha un inverso naturale pulito (richiamare lo stesso intent con "enabled"
# invertito, nessun upsert coinvolto) ma e' stata deliberatamente ESCLUSA: l'unico modo per farla
# partecipare all'undo utente-iniziato (F1.3.5) e' registrarla anche qui sotto, che la rende PERO'
# automaticamente candidata anche al rollback AUTOMATICO di un compito multi-passo interrotto
# (vedi TaskAgent._rollback in core/agent.py) - i due meccanismi condividono lo stesso
# `IntentSafetyEntry.rollback`, non sono disattivabili indipendentemente in questa architettura.
# Riattivare la modalita' privata da sola in automatico perche' un passo SUCCESSIVO e scollegato
# di un compito e' fallito non e' un default sicuro per un controllo di privacy (Gate G1, settimo
# criterio, gia' superato - non va destabilizzato di riflesso qui): resta un candidato dichiarato
# per un futuro incremento che disaccoppi i due percorsi, non una svista.
def _text_undo_params(data: dict) -> dict:
    """Per le tre coppie sotto (ADD_TODO/SET_REMINDER/SET_DAILY_REMINDER) che condividono la
    stessa forma "cerca per testo e cancella" gia' usata dalle rispettive skill DELETE_*/CANCEL_*
    per l'uso manuale - lo stesso limite di quella ricerca per somiglianza (potrebbe in teoria
    corrispondere a una voce preesistente con un testo simile invece di quella appena creata) resta
    IDENTICO a quello gia' accettato per l'uso manuale della stessa skill, non un rischio nuovo
    introdotto dall'undo."""
    return {"text": data["text"]}


def _rollback_add_todo(registry, data, policy_engine):
    registry.execute("DELETE_TODO", _text_undo_params(data), policy_engine=policy_engine)


def _rollback_set_reminder(registry, data, policy_engine):
    registry.execute("DELETE_REMINDER", _text_undo_params(data), policy_engine=policy_engine)


def _rollback_set_daily_reminder(registry, data, policy_engine):
    registry.execute("DELETE_REMINDER", _text_undo_params(data), policy_engine=policy_engine)


def _timer_label_undo_params(data: dict) -> dict:
    return {"label": data["label"]}


def _rollback_set_timer(registry, data, policy_engine):
    registry.execute("CANCEL_TIMER", _timer_label_undo_params(data), policy_engine=policy_engine)


def _pomodoro_undo_params(data: dict) -> dict:
    # StopPomodoroSkill non ha parametri (cerca sempre per l'etichetta fissa "pomodoro") - la
    # funzione esiste comunque, invece di omettere la coppia in UNDO_PARAMS_BY_INTENT, per lo
    # stesso motivo di coerenza di struttura delle altre nove: un dizionario vuoto e' comunque un
    # calcolo esplicito, non un'omissione.
    return {}


def _rollback_start_pomodoro(registry, data, policy_engine):
    registry.execute("STOP_POMODORO", _pomodoro_undo_params(data), policy_engine=policy_engine)


def _window_title_undo_params(data: dict) -> dict:
    return {"title": data["title"]}


def _rollback_maximize_window(registry, data, policy_engine):
    registry.execute("RESTORE_WINDOW", _window_title_undo_params(data), policy_engine=policy_engine)


def _rollback_minimize_window(registry, data, policy_engine):
    registry.execute("RESTORE_WINDOW", _window_title_undo_params(data), policy_engine=policy_engine)


def _take_screenshot_undo_params(data: dict) -> dict:
    return {"path": data["path"], "confirmed": True}


def _rollback_take_screenshot(registry, data, policy_engine):
    registry.execute("DELETE_PATH", _take_screenshot_undo_params(data), policy_engine=policy_engine)


def _duplicate_file_undo_params(data: dict) -> dict:
    # A differenza di CREATE_PATH/TAKE_SCREENSHOT, il file da cancellare per annullare NON e'
    # "path" (l'originale, che deve restare intatto) ma "destination" (la copia appena creata) -
    # DuplicateFileSkill sceglie sempre un nome NON in collisione (" - copia", " - copia 2", ...
    # verificato leggendo skills/file_utils.py::DuplicateFileSkill.execute()), quindi non c'e' mai
    # il rischio di upsert gia' escluso sopra per COMPRESS_PATH/EXPORT_NOTES.
    return {"path": data["destination"], "confirmed": True}


def _rollback_duplicate_file(registry, data, policy_engine):
    registry.execute("DELETE_PATH", _duplicate_file_undo_params(data), policy_engine=policy_engine)


def _toggle_dark_mode_undo_params(data: dict) -> dict:
    return {"enabled": not data["enabled"]}


def _rollback_toggle_dark_mode(registry, data, policy_engine):
    registry.execute("TOGGLE_DARK_MODE", _toggle_dark_mode_undo_params(data), policy_engine=policy_engine)


# F1.3.5: {intent originale: funzione pura che calcola i parametri dell'intent compensatorio dai
# dati della ricevuta originale} - lo stesso identico calcolo gia' usato da ciascun
# "_rollback_*" sopra (che li riusa direttamente, non li duplica), esposto anche per
# core/undo_store.py::generate_undo_descriptor() (pubblico, non con un trattino basso, proprio
# perche' un modulo esterno lo consuma - stesso principio di INTENT_SAFETY_REGISTRY). Solo gli
# intent che hanno gia' un `RollbackAction` qui sotto: un intent senza inverso naturale (es.
# DELETE_PATH) non ha un compensating_parameters sensato da calcolare.
UNDO_PARAMS_BY_INTENT: dict[str, Callable[[dict], dict]] = {
    "CREATE_PATH": _create_path_undo_params,
    "RENAME_PATH": _rename_path_undo_params,
    "MOVE_PATH": _move_path_undo_params,
    "EXTRACT_ARCHIVE": _extract_archive_undo_params,
    "ADD_TODO": _text_undo_params,
    "SET_REMINDER": _text_undo_params,
    "SET_DAILY_REMINDER": _text_undo_params,
    "SET_TIMER": _timer_label_undo_params,
    "START_POMODORO": _pomodoro_undo_params,
    "MAXIMIZE_WINDOW": _window_title_undo_params,
    "MINIMIZE_WINDOW": _window_title_undo_params,
    "TAKE_SCREENSHOT": _take_screenshot_undo_params,
    "DUPLICATE_FILE": _duplicate_file_undo_params,
    "TOGGLE_DARK_MODE": _toggle_dark_mode_undo_params,
}


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


def _verify_explorer_running(data: dict) -> bool:
    """F1.3.2 (stesso pattern trovato una quarta volta in questa sessione): controlla per
    davvero che almeno un processo explorer.exe esista, invece di fidarsi del successo
    dichiarato da RestartExplorerSkill (che dalla correzione in skills/system_maintenance.py
    attende gia' lei stessa la ricomparsa del processo prima di dichiarare successo - questo e'
    un secondo controllo indipendente, non l'unico, stesso principio delle due funzioni sopra).
    Ignora `data` (sempre {} - RESTART_EXPLORER non ha un PID da riportare, a differenza di
    KILL_PROCESS_BY_PORT: non si conosce in anticipo quale sara' il nuovo processo)."""
    import psutil

    for process in psutil.process_iter(["name"]):
        try:
            if (process.info.get("name") or "").lower() == "explorer.exe":
                return True
        except psutil.Error:
            continue
    return False


class _SHQUERYRBINFO(ctypes.Structure):
    """Layout nativo di SHQUERYRBINFO (shell32.dll), stesso ordine/tipi della struct C -
    cbSize deve essere impostato dal chiamante PRIMA della chiamata (SHQueryRecycleBinW lo usa
    per riconoscere la versione della struct, stesso pattern di molte API Win32 "sized")."""

    _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]


def _verify_recycle_bin_empty(data: dict) -> bool:
    """F1.3.2 (Gate G1, secondo criterio): controlla per davvero, con una seconda chiamata
    all'API nativa (SHQueryRecycleBinW, sola lettura - non SHEmptyRecycleBinW che l'ha gia'
    svuotato), che il cestino non contenga piu' elementi - invece di fidarsi soltanto del codice
    di ritorno di SHEmptyRecycleBinW gia' controllato da EmptyRecycleBinSkill (skills/
    recycle_bin.py). Diverso dagli altri tre verificatori sopra: SHEmptyRecycleBinW e' gia'
    SINCRONA per contratto documentato (non fire-and-forget come PostMessage/Popen/una chiamata
    HTTP), quindi qui non c'e' un buco "dichiara successo senza aspettare" da correggere nella
    skill - solo una seconda prova indipendente in piu', stesso principio di
    _verify_process_terminated. pointer() invece di byref(): serve .contents per leggere il
    risultato scritto dalla chiamata (byref() non e' dereferenziabile in Python, solo passabile
    a una funzione C). Un esito HRESULT diverso da S_OK (0) - querying fallito - conta come NON
    verificato (fail-closed, stesso principio "negare per default" gia' applicato altrove in
    questo modulo), mai come "assumo vada bene"."""
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    result = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.pointer(info))
    return result == 0 and info.i64NumItems == 0


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
    "RESTART_EXPLORER": IntentSafetyEntry(
        verifier=_verify_explorer_running,
        rollback=None,  # riavviare non ha un inverso: non si puo' "de-riavviare" un processo
    ),
    "EMPTY_RECYCLE_BIN": IntentSafetyEntry(
        verifier=_verify_recycle_bin_empty,
        rollback=None,  # svuotare il cestino e' irreversibile per definizione (SHERB_NOCONFIRMATION)
    ),
    "DELETE_PATH": IntentSafetyEntry(
        verifier=lambda data: not Path(data["path"]).exists(),
        rollback=None,  # cancellare non ha un inverso naturale
    ),
    # F1 (Gate G1, secondo criterio - "tutte le azioni ad alto impatto hanno prova e audit"):
    # investigati tutti i 20 intent DESTRUCTIVE/ADMIN (core/risk.py). La maggior parte cancella
    # un elemento da uno STORE INTERNO (FORGET/CLEAR_NOTES/DELETE_TODO/DELETE_TRIGGER/DELETE_
    # REMINDER/FORGET_LEARNED): scartati deliberatamente - un verificatore li' richiederebbe
    # iniettare il manager corrispondente in verify_effect(), la stessa dipendenza esterna gia'
    # rifiutata per Home Assistant (F1.3.2 casa), E il bool di successo che restituiscono e' gia'
    # derivato da cursor.rowcount/una SELECT reale sulla stessa connessione - non il buco
    # "successo dichiarato ma mai controllato" che ha motivato gli altri verificatori. Le tre
    # sotto invece toccano il FILESYSTEM (come CREATE_PATH/DELETE_PATH sopra): una prova
    # indipendente e senza dipendenze e' possibile allo stesso modo.
    "EXTRACT_ARCHIVE": IntentSafetyEntry(
        # is_dir() da solo non basta: la cartella di destinazione (path.with_suffix(""))
        # potrebbe gia' esistere vuota da prima per un altro motivo - un archivio estratto con
        # successo la popola sempre di almeno un elemento (anche un archivio vuoto produce la
        # cartella stessa creata da shutil.unpack_archive, ma qui si vuole la prova che
        # l'estrazione abbia scritto qualcosa, non solo che la cartella esista).
        verifier=lambda data: Path(data["destination"]).is_dir() and any(Path(data["destination"]).iterdir()),
        rollback=RollbackAction(_rollback_extract_archive, "DELETE_PATH"),
    ),
    "CREATE_SKILL": IntentSafetyEntry(
        verifier=lambda data: Path(data["path"]).exists(),
        # Nessun inverso automatico: DELETE_CREATED_SKILL cerca per somiglianza su nome/intent/
        # descrizione (fuzzy, non un percorso esatto) e disiscrive anche l'intent dal registro -
        # comporre qui un rollback che cancella solo il FILE senza passare da forge.delete()
        # lascerebbe l'intent ancora registrato, uno stato peggiore di non annullare affatto.
        rollback=None,
    ),
    "DELETE_CREATED_SKILL": IntentSafetyEntry(
        verifier=lambda data: not Path(data["path"]).exists(),
        rollback=None,  # cancellare una skill non ha un inverso naturale, stesso principio di DELETE_PATH
    ),
    # F1.3 (criterio "80% delle azioni reversibili" - vedi il commento sopra UNDO_PARAMS_BY_INTENT
    # per il criterio di selezione ed esclusione applicato a tutte le 67 intent LOCAL_REVERSIBLE):
    # nessuno di questi dieci ha un verificatore indipendente (verifier=None, come la maggioranza
    # gia' in questo registry) - solo un inverso naturale, per l'undo utente-iniziato (F1.3.5) e,
    # di conseguenza, anche per il rollback automatico di un compito multi-passo interrotto
    # (TaskAgent._rollback/PlanExecutor, gia' esistente per CREATE_PATH/RENAME_PATH/MOVE_PATH
    # sopra - stessa semantica estesa, non un meccanismo nuovo).
    "ADD_TODO": IntentSafetyEntry(rollback=RollbackAction(_rollback_add_todo, "DELETE_TODO")),
    "SET_REMINDER": IntentSafetyEntry(rollback=RollbackAction(_rollback_set_reminder, "DELETE_REMINDER")),
    "SET_DAILY_REMINDER": IntentSafetyEntry(rollback=RollbackAction(_rollback_set_daily_reminder, "DELETE_REMINDER")),
    "SET_TIMER": IntentSafetyEntry(rollback=RollbackAction(_rollback_set_timer, "CANCEL_TIMER")),
    "START_POMODORO": IntentSafetyEntry(rollback=RollbackAction(_rollback_start_pomodoro, "STOP_POMODORO")),
    "MAXIMIZE_WINDOW": IntentSafetyEntry(rollback=RollbackAction(_rollback_maximize_window, "RESTORE_WINDOW")),
    "MINIMIZE_WINDOW": IntentSafetyEntry(rollback=RollbackAction(_rollback_minimize_window, "RESTORE_WINDOW")),
    "TAKE_SCREENSHOT": IntentSafetyEntry(rollback=RollbackAction(_rollback_take_screenshot, "DELETE_PATH")),
    "DUPLICATE_FILE": IntentSafetyEntry(rollback=RollbackAction(_rollback_duplicate_file, "DELETE_PATH")),
    "TOGGLE_DARK_MODE": IntentSafetyEntry(rollback=RollbackAction(_rollback_toggle_dark_mode, "TOGGLE_DARK_MODE")),
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
        # Un compito annullato con "Jake, basta" deve poter rimettere a posto cio' che aveva gia'
        # fatto: la compensazione non e' un'azione nuova del turno annullato.
        with cancellation_suspended():
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
