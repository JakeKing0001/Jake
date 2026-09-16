"""Snapshot minimo prima di un'azione (F1.3.4, "salvare snapshot minimo prima dell'azione,
rispettando privacy e dimensione" - vedi ROADMAP_EXECUTION.md sezione F1.3).

Un problema DIVERSO da F1.3.5 (`core/undo_store.py`): li' si calcola l'intent/i parametri per
COMPENSARE un'azione gia' riuscita (es. DELETE_PATH per annullare un CREATE_PATH) - un inverso
naturale che non richiede aver visto lo stato PRIMA. Qui invece si cattura lo stato PRIMA che
un'azione lo muti, per le azioni che un inverso naturale non ce l'hanno affatto - DELETE_PATH e'
l'esempio dichiarato (`generate_undo_descriptor("DELETE_PATH", ...)` restituisce sempre `None`,
vedi `tests/test_undo_store.py`): senza aver salvato il contenuto PRIMA della cancellazione, non
c'e' nessun modo di recuperarlo dopo, qualunque intent compensatorio si inventi.

Deliberatamente NON affrontato qui (passo successivo dichiarato, stesso principio "prima il
meccanismo, poi l'adozione" gia' seguito per `UndoStore`/`ResourceLockManager`/`TaskRiskBudget` in
questa sessione): nessun collegamento ai chokepoint reali che potrebbero catturare uno snapshot
prima di un'azione mutante, ne' un modo di RIPRISTINARLO - solo catturarlo e conservarlo. Restituire
lo stato a un file da uno snapshot e' un problema a se' (quale intent lo farebbe? con quale
conferma?), non affrontato qui. Scope deliberatamente ristretto ai FILE (non alle cartelle: una
cartella intera potrebbe contenere qualunque quantita' di dati, "minimo" per definizione esclude
una copia ricorsiva non limitata - snapshot di una cartella dichiarato fuori scope)."""
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# "Minimo" per davvero: un file piu' grande di questo non viene mai copiato, ne' troncato - uno
# snapshot TRONCATO di un file sarebbe peggio di nessuno snapshot (un ripristino silenziosamente
# corrotto e' un danno nuovo, non solo l'assenza del vecchio). Un default ragionevole ("prima deve
# funzionare", stesso principio gia' usato per altri limiti in questa sessione - es. UndoStore.
# DEFAULT_UNDO_TTL_SECONDS), non una policy definitiva.
DEFAULT_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ActionSnapshot:
    action_id: str
    path: str
    content: bytes
    size_bytes: int
    captured_at: float


def capture_snapshot(
    action_id: str, path: str, *,
    private: bool = False, max_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES, now: Optional[float] = None,
) -> Optional[ActionSnapshot]:
    """Uno snapshot VERO del contenuto di `path` prima che un'azione lo muti, o `None` (onesto
    "non posso catturarlo", mai un valore parziale/indovinato) se `private` e' vero, il percorso
    non e' un file esistente, o il file supera `max_bytes`.

    `private=True` esce PRIMA di toccare il filesystem, non solo prima di persistere il risultato
    - stessa garanzia gia' data altrove per la modalita' privata (`core/action_ledger.py::record`,
    `JakeCore._answer_inner`): uno scambio in modalita' privata non deve lasciare traccia da
    nessuna parte, e leggere comunque i byte di un file solo per scartarli subito dopo sarebbe
    comunque un accesso avvenuto, anche se il risultato non sopravvive."""
    if private:
        return None
    target = Path(path)
    if not target.is_file():
        return None
    try:
        if target.stat().st_size > max_bytes:
            return None
        content = target.read_bytes()
    except OSError:
        return None
    if len(content) > max_bytes:
        # Raro (una scrittura concorrente tra lo stat() e la read_bytes() sopra puo' far
        # crescere il file) ma possibile - stessa scelta "onesto None, mai un valore parziale"
        # del controllo sulla dimensione dichiarata dallo stat().
        return None
    return ActionSnapshot(
        action_id=action_id, path=str(target), content=content,
        size_bytes=len(content), captured_at=now if now is not None else time.time(),
    )


class SnapshotStore:
    """Uno `ActionSnapshot` per `action_id`, creato pigramente e mai ripulito da solo - lo stesso
    principio gia' accettato per `core.undo_store.UndoStore`/`core.resource_lock.
    ResourceLockManager` (il numero di azioni con uno snapshot in sospeso contemporaneamente resta
    piccolo per un assistente personale). Un `threading.Lock` perche' i chokepoint di produzione
    (JakeCore/TaskAgent/PlanExecutor, quando adottato) girano su thread diversi."""

    def __init__(self) -> None:
        self._snapshots: dict[str, ActionSnapshot] = {}
        self._lock = threading.Lock()

    def save(self, snapshot: ActionSnapshot) -> None:
        with self._lock:
            self._snapshots[snapshot.action_id] = snapshot

    def get(self, action_id: str) -> Optional[ActionSnapshot]:
        with self._lock:
            return self._snapshots.get(action_id)
