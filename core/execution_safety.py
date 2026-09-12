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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.skill_result import SkillResult

RETRYABLE_ERRORS = {"OPERATION_FAILED", "NETWORK_UNAVAILABLE"}
MAX_ATTEMPTS = 2


def execute_with_retry(execute_fn, intent: str, parameters: dict) -> tuple[SkillResult, int]:
    """Chiama execute_fn(intent, parameters), ritentando fino a MAX_ATTEMPTS volte se
    l'errore e' tra quelli transitori (RETRYABLE_ERRORS). execute_fn e' un callable qualsiasi
    (SkillRegistry.execute, o il ripiego piu' ricco di JakeCore._resolve_and_execute): questa
    funzione non sa e non le importa cosa faccia davvero, ritenta solo in base al risultato.
    Restituisce (risultato, tentativi fatti)."""
    attempts = 0
    result: SkillResult | None = None
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        result = execute_fn(intent, parameters)
        if result is None:
            result = SkillResult(success=False, data={}, error="UNKNOWN_INTENT")
            break
        if result.success or result.error not in RETRYABLE_ERRORS:
            break
    # MAX_ATTEMPTS >= 1 garantisce che il ciclo giri almeno una volta, quindi result non e' mai
    # None qui davvero - ma un ripiego esplicito (invece di fidarsi solo di quell'invariante)
    # evita che un futuro MAX_ATTEMPTS = 0 restituisca None a un chiamante che si aspetta sempre
    # un vero SkillResult.
    if result is None:
        result = SkillResult(success=False, data={}, error="UNKNOWN_INTENT")
    return result, attempts


def _rollback_create_path(registry, data):
    registry.execute("DELETE_PATH", {"path": data["path"], "confirmed": True})


def _rollback_rename_path(registry, data):
    original_name = Path(data["path"]).name
    registry.execute("RENAME_PATH", {"path": data["new_path"], "new_name": original_name})


def _rollback_move_path(registry, data):
    original_dir = str(Path(data["path"]).parent)
    registry.execute("MOVE_PATH", {"path": data["new_path"], "destination": original_dir, "confirmed": True})


@dataclass(frozen=True)
class RollbackAction:
    """L'inverso naturale di un intent gia' eseguito con successo, e l'intent che DAVVERO esegue
    (i tre handler sotto chiamano registry.execute() con "confirmed": True gia' impostato, per
    compensare un effetto gia' approvato senza bloccarsi in attesa di una conferma che qui
    nessuno puo' dare - vedi rollback_effect). compensating_intent serve a controllare
    blocked_intents PRIMA di chiamare handler, non dopo (F1.2.5)."""

    handler: Callable[[object, dict], None]
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


# Solo le operazioni filesystem hanno oggi un effetto verificabile senza dipendenze aggiuntive
# (per lo schermo/UI arrivera' con il Computer Use Engine, fase 3.7) e/o un inverso naturale: le
# altre (aprire un'app, cercare, ricordare un'informazione, ...) non hanno ne' l'uno ne' l'altro
# e restano fuori da questo registry (verify_effect/rollback_effect le trattano di conseguenza).
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


def rollback_effect(registry, intent: str, data: dict, policy_engine=None) -> bool:
    """Annulla l'effetto di un passo gia' eseguito con successo, se esiste un inverso noto per
    il suo intent (vedi INTENT_SAFETY_REGISTRY). Vero se e' stato davvero annullato; gli errori
    nel rollback stesso vengono inghiottiti (un rollback fallito non deve mai far crashare il
    chiamante, ne' mascherare l'errore originale che ha scatenato il rollback).

    F1.2.5: policy_engine (core/policy_engine.py::PolicyEngine) e' opzionale per compatibilita'
    con i chiamanti che non ne hanno ancora uno da passare, ma quando c'e' un blocked_intents
    che include l'intent compensatorio, il rollback NON parte: prima di questa correzione i tre
    handler chiamavano registry.execute() direttamente, con "confirmed": True auto-iniettato,
    bypassando PolicyEngine del tutto - un DELETE_PATH disabilitato dall'utente restava comunque
    eseguibile come "annullamento" di un CREATE_PATH. Non si passa invece da decide_automated():
    quello richiederebbe CONFIRM per un intent DESTRUCTIVE/ADMIN, ma qui nessun utente e' pronto
    a confermare in tempo reale - bloccare resta l'unico controllo che ha senso applicare a
    un'azione gia' approvata in origine."""
    entry = INTENT_SAFETY_REGISTRY.get(intent)
    if entry is None or entry.rollback is None:
        return False
    if policy_engine is not None and entry.rollback.compensating_intent in policy_engine.blocked_intents:
        return False
    try:
        entry.rollback.handler(registry, data)
        return True
    except Exception:
        return False
