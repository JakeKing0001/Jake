"""Generazione e memorizzazione di undo token (F1.3.5, "generare undo token con scadenza e
precondizioni" - vedi ROADMAP_EXECUTION.md sezione F1.3).

Prima di questo modulo, `core.action_contracts.UndoDescriptor` esisteva SOLO come contratto dati
(F1.1.2) - mai costruito in nessun punto di produzione (verificato con un grep letterale prima di
scrivere questo modulo: zero istanze di `UndoDescriptor(` fuori dalla sua stessa definizione e dai
suoi test). `core.execution_safety.py::rollback_effect()` sa gia' come calcolare i parametri di
un intent compensatorio da un risultato riuscito - ma SOLO per il percorso di compensazione
AUTOMATICA innescata da una verifica fallita, mai per un'azione riuscita che l'utente potrebbe
voler annullare in seguito di sua scelta. `generate_undo_descriptor()` riusa lo STESSO calcolo
(`execution_safety.UNDO_PARAMS_BY_INTENT`, le funzioni pure gia' estratte dai quattro handler di
rollback) invece di duplicarlo una seconda volta.

Vive in un modulo a parte (non dentro execution_safety.py) per evitare un import circolare:
`core.action_contracts` importa gia' da `core.execution_safety` (RETRYABLE_ERRORS), quindi
`execution_safety.py` non puo' importare `UndoDescriptor` da `action_contracts.py` senza un
ciclo - questo modulo importa da ENTRAMBI, nessuno dei due importa da qui.

Deliberatamente NON affrontato qui (passo successivo dichiarato, stesso principio "prima il
meccanismo, poi l'adozione" gia' seguito per ResourceLockManager/TaskRiskBudget in questa
sessione): nessun collegamento ai tre chokepoint reali (JakeCore/TaskAgent/PlanExecutor) che
potrebbero popolare questo store dopo un'azione riuscita, ne' una skill "ANNULLA" che lo
consumi; `UndoDescriptor.preconditions` resta sempre `None` - descrivere COSA deve restare vero
perche' l'undo abbia senso dipende dai parametri della singola chiamata, lo stesso giudizio caso
per caso gia' rifiutato per `ActionProposal.preconditions`/`expected_effect` (F1.1.7)."""
import threading
import time
from typing import Optional

from core.action_contracts import UndoDescriptor
from core.execution_safety import INTENT_SAFETY_REGISTRY, UNDO_PARAMS_BY_INTENT

# Quanto resta valido un undo prima di scadere. Un default ragionevole ("prima deve funzionare",
# stesso principio gia' usato per altri TTL in questa sessione - es. PairingChallenge, 5 minuti),
# non una policy definitiva: nessun intent esistente aveva mai bisogno di questo numero prima
# d'ora, quindi non esiste un valore "corretto" da scoprire, solo uno ragionevole da dichiarare
# esplicitamente.
DEFAULT_UNDO_TTL_SECONDS = 5 * 60


def generate_undo_descriptor(
    action_id: str, intent: str, data: dict, *,
    ttl_seconds: float = DEFAULT_UNDO_TTL_SECONDS, now: Optional[float] = None,
) -> Optional[UndoDescriptor]:
    """Un vero `UndoDescriptor` per un'azione GIA' eseguita con successo, se il suo intent ha un
    inverso naturale gia' noto (`INTENT_SAFETY_REGISTRY`). `None` (non un valore indovinato) per
    un intent senza inverso naturale, o se `data` non contiene le chiavi attese per calcolare i
    parametri compensatori (es. un risultato malformato/parziale) - onesto "non posso generarlo",
    mai un `UndoDescriptor` con parametri inventati che fallirebbe silenziosamente se mai usato."""
    params_fn = UNDO_PARAMS_BY_INTENT.get(intent)
    safety_entry = INTENT_SAFETY_REGISTRY.get(intent)
    if params_fn is None or safety_entry is None or safety_entry.rollback is None:
        return None
    try:
        compensating_parameters = params_fn(data)
    except (KeyError, TypeError):
        return None
    effective_now = now if now is not None else time.time()
    return UndoDescriptor(
        action_id=action_id,
        compensating_intent=safety_entry.rollback.compensating_intent,
        compensating_parameters=compensating_parameters,
        expires_at=effective_now + ttl_seconds,
    )


class UndoStore:
    """Un `UndoDescriptor` per `action_id`, creato pigramente e mai ripulito da solo - lo stesso
    principio gia' accettato per `core.resource_lock.ResourceLockManager` (il numero di azioni
    annullabili in sospeso contemporaneamente resta piccolo per un assistente personale, un
    dizionario che cresce nel tempo non e' un problema pratico qui). Un `threading.Lock` perche'
    i tre chokepoint di produzione (JakeCore/TaskAgent/PlanExecutor) girano su thread diversi."""

    def __init__(self) -> None:
        self._descriptors: dict[str, UndoDescriptor] = {}
        self._lock = threading.Lock()

    def save(self, descriptor: UndoDescriptor) -> None:
        with self._lock:
            self._descriptors[descriptor.action_id] = descriptor

    def get(self, action_id: str, *, now: Optional[float] = None) -> Optional[UndoDescriptor]:
        """`None` sia se non esiste sia se non e' piu' usabile (scaduto o gia' consumato) - un
        chiamante non deve distinguere i due casi, in entrambi non c'e' nulla da annullare."""
        with self._lock:
            descriptor = self._descriptors.get(action_id)
        if descriptor is None or not descriptor.is_usable(now=now):
            return None
        return descriptor

    def mark_used(self, action_id: str) -> bool:
        """Consuma l'undo (un solo utilizzo, mai due) - vero se c'era davvero un descrittore
        usabile da consumare, falso altrimenti (gia' consumato, scaduto, o mai esistito)."""
        with self._lock:
            descriptor = self._descriptors.get(action_id)
            if descriptor is None or not descriptor.is_usable():
                return False
            self._descriptors[action_id] = UndoDescriptor(
                action_id=descriptor.action_id,
                compensating_intent=descriptor.compensating_intent,
                compensating_parameters=descriptor.compensating_parameters,
                expires_at=descriptor.expires_at,
                preconditions=descriptor.preconditions,
                used=True,
            )
            return True
