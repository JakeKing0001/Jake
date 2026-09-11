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
from pathlib import Path

from core.skill_result import SkillResult

RETRYABLE_ERRORS = {"OPERATION_FAILED", "NETWORK_UNAVAILABLE"}
MAX_ATTEMPTS = 2
# Intent per cui verify_effect esegue un controllo indipendente vero (oggi solo il filesystem):
# usato dai due esecutori (core/agent.py, core/plan_executor.py) per decidere quando il log
# strutturato (F0, core/logger.log_action) puo' scrivere verified=True/False invece di lasciarlo
# assente - verify_effect restituisce True anche quando non ha nessuna verifica da fare, e
# riportarlo come verified=True affermerebbe una prova mai avvenuta per ogni altro intent.
VERIFIABLE_INTENTS = {"CREATE_PATH", "RENAME_PATH", "MOVE_PATH", "DELETE_PATH"}


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


def verify_effect(intent: str, data: dict) -> bool:
    """Controlla in modo indipendente che l'effetto dichiarato da una skill sia davvero
    avvenuto (v1.5), invece di fidarsi ciecamente del 'success' riportato: oggi solo per le
    operazioni sul filesystem, le uniche con un effetto verificabile qui senza dipendenze
    aggiuntive (per lo schermo/UI arrivera' con il Computer Use Engine, fase 3.7)."""
    if intent == "CREATE_PATH":
        return Path(data["path"]).exists()
    if intent in ("RENAME_PATH", "MOVE_PATH"):
        return Path(data["new_path"]).exists()
    if intent == "DELETE_PATH":
        return not Path(data["path"]).exists()
    return True  # nessuna verifica indipendente disponibile per questo intent


def _rollback_create_path(registry, data):
    registry.execute("DELETE_PATH", {"path": data["path"], "confirmed": True})


def _rollback_rename_path(registry, data):
    original_name = Path(data["path"]).name
    registry.execute("RENAME_PATH", {"path": data["new_path"], "new_name": original_name})


def _rollback_move_path(registry, data):
    original_dir = str(Path(data["path"]).parent)
    registry.execute("MOVE_PATH", {"path": data["new_path"], "destination": original_dir, "confirmed": True})


# Solo le operazioni filesystem con un inverso naturale sono annullabili: le altre (aprire
# un'app, cercare, ricordare un'informazione, ...) non hanno un rollback sensato e vengono
# lasciate cosi' come sono, come previsto dalla roadmap ("rollback dove possibile").
ROLLBACK_HANDLERS = {
    "CREATE_PATH": _rollback_create_path,
    "RENAME_PATH": _rollback_rename_path,
    "MOVE_PATH": _rollback_move_path,
}

# F1.2.5: quale intent esegue DAVVERO ogni handler (i tre sopra chiamano registry.execute() con
# "confirmed": True gia' impostato, per compensare un effetto gia' approvato senza bloccarsi in
# attesa di una conferma che qui nessuno puo' dare - vedi rollback_effect). Serve un elenco
# separato dagli handler stessi per poter controllare blocked_intents PRIMA di chiamarli, non
# dopo: un intent che l'utente ha esplicitamente disabilitato in config.json non deve eseguire
# nemmeno come compensazione di un passo gia' fatto.
ROLLBACK_COMPENSATING_INTENT = {
    "CREATE_PATH": "DELETE_PATH",
    "RENAME_PATH": "RENAME_PATH",
    "MOVE_PATH": "MOVE_PATH",
}


def rollback_effect(registry, intent: str, data: dict, policy_engine=None) -> bool:
    """Annulla l'effetto di un passo gia' eseguito con successo, se esiste un inverso noto per
    il suo intent (vedi ROLLBACK_HANDLERS). Vero se e' stato davvero annullato; gli errori nel
    rollback stesso vengono inghiottiti (un rollback fallito non deve mai far crashare il
    chiamante, ne' mascherare l'errore originale che ha scatenato il rollback).

    F1.2.5: policy_engine (core/policy_engine.py::PolicyEngine) e' opzionale per compatibilita'
    con i chiamanti che non ne hanno ancora uno da passare, ma quando c'e' un blocked_intents
    che include l'intent compensatorio (vedi ROLLBACK_COMPENSATING_INTENT), il rollback NON
    parte: prima di questa correzione i tre handler chiamavano registry.execute() direttamente,
    con "confirmed": True auto-iniettato, bypassando PolicyEngine del tutto - un DELETE_PATH
    disabilitato dall'utente restava comunque eseguibile come "annullamento" di un CREATE_PATH.
    Non si passa invece da decide_automated(): quello richiederebbe CONFIRM per un intent
    DESTRUCTIVE/ADMIN, ma qui nessun utente e' pronto a confermare in tempo reale - bloccare
    resta l'unico controllo che ha senso applicare a un'azione gia' approvata in origine."""
    handler = ROLLBACK_HANDLERS.get(intent)
    if handler is None:
        return False
    compensating_intent = ROLLBACK_COMPENSATING_INTENT[intent]
    if policy_engine is not None and compensating_intent in policy_engine.blocked_intents:
        return False
    try:
        handler(registry, data)
        return True
    except Exception:
        return False
