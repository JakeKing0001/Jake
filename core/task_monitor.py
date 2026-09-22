"""Monitor di compiti lunghi e digital housekeeping (F6.7), senza loop di notifica e senza cancellazione
autonoma di dati - le due garanzie che il criterio di uscita della fase chiede esplicitamente.

- F6.7.1/F6.7.2 `TaskMonitorRegistry`: segue compiti lunghi (un download, un'indicizzazione, un backup) SENZA
  notificare a ogni progresso - `progress()` aggiorna lo stato silenziosamente; un evento da notificare nasce
  SOLO da una transizione (completamento, errore, anomalia, decisione richiesta) e ognuna e' idempotente: la
  stessa transizione richiamata di nuovo non produce un secondo evento (il "loop di notifica" che il criterio
  di uscita vieta).
- F6.7.3 `HousekeepingScanner`: trova duplicati, backup vecchi, aggiornamenti disponibili e problemi di postura
  di sicurezza - da record che il CHIAMANTE fornisce (hash/dimensione dei file, ultimo backup riuscito, versioni
  installate/disponibili, esiti di controlli di sicurezza). Nessuna scansione del filesystem o chiamata di rete
  avviene in questo modulo: e' analisi pura su dati dichiarati, stesso principio "il rischio si dichiara, non
  si deduce dal contenuto" gia' applicato altrove in Jake.
- F6.7.4/F6.7.5 `HousekeepingPlan`: suggerimento (`Finding.description`), anteprima (`preview()`, cosa
  cambierebbe: percorsi e byte, MAI eseguito) e azione sono tre cose separate. **Il modulo non puo' cancellare
  o spostare nulla da solo**: `apply()` richiede un'approvazione esplicita legata al digest esatto del piano
  (stesso schema di `core/skill_package.py::UserApproval`) e delega ogni azione a un `executor` iniettato dal
  chiamante - nessun `os.remove`/`shutil` in questo file.
- F6.7.6 `AutonomyBudget`: oltre alla frequenza (gia' `core/autonomy_budget.py`, qui riusata), un piano
  dichiara il proprio costo su piu' dimensioni (byte spostati, secondi stimati, chiamate di rete, "impatto" -
  un punteggio dichiarato dal chiamante) e il budget lo rifiuta PRIMA di applicarlo se una qualunque dimensione
  supererebbe il limite nella finestra corrente.
- F6.7.7 `MonitorStore`: chiude un monitor quando il compito finisce per qualunque motivo; un monitor ancora
  `RUNNING` quando Jake si e' fermato in modo anomalo sopravvive su disco (stesso pattern gia' usato da
  `core/agent_checkpoint.py`) e viene offerto a chi legge lo store al riavvio - la ripresa VERA del compito
  resta specifica del compito e non e' implementata qui (limite dichiarato sotto).

Limiti dichiarati: nessun collegamento a `JakeCore`/HUD (nessun evento pubblicato su `core/event_bus.py`);
nessuna scansione reale del filesystem/rete/versioni software; `MonitorStore.resume_candidates()` restituisce
solo I DATI del monitor interrotto, non lo riprende da solo (F6.7.7 vieta esplicitamente una ripresa
automatica di un'azione, stesso principio gia' applicato in `core/agent_checkpoint.py`)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

DEFAULT_MONITOR_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_task_monitors.json"


# ---- F6.7.1/F6.7.2: monitor senza notifiche ripetitive ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    ANOMALY = "anomaly"
    NEEDS_DECISION = "needs_decision"


_TERMINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.ERROR})
# Le transizioni che generano un evento da notificare (F6.7.2): mai un semplice "progresso".
_NOTIFY_ON = frozenset({TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.ANOMALY, TaskStatus.NEEDS_DECISION})


@dataclass
class MonitoredTask:
    task_id: str
    label: str
    status: TaskStatus
    started_at: float
    last_progress_at: float
    message: str = ""
    # Numero di transizioni notificabili gia' emesse per questo task: usato per l'idempotenza (F6.7.2).
    _notified_transitions: int = 0


@dataclass(frozen=True)
class MonitorEvent:
    task_id: str
    label: str
    status: TaskStatus
    message: str
    at: float


class UnknownTaskError(KeyError):
    pass


class TaskMonitorRegistry:
    """Un registro in memoria di compiti in corso. Thread-safe (un download e un'indicizzazione possono
    aggiornare il proprio stato da thread diversi)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._tasks: dict[str, MonitoredTask] = {}
        self._pending_events: deque[MonitorEvent] = deque()

    def start(self, task_id: str, label: str) -> MonitoredTask:
        with self._lock:
            if task_id in self._tasks:
                raise ValueError(f"task_id gia' in uso: {task_id}")
            now = self._clock()
            task = MonitoredTask(task_id, label, TaskStatus.RUNNING, now, now)
            self._tasks[task_id] = task
            return task

    def _require(self, task_id: str) -> MonitoredTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise UnknownTaskError(task_id)
        return task

    def progress(self, task_id: str, message: str = "") -> None:
        """Aggiorna la recenza SENZA generare alcun evento - F6.7.1: seguire un compito lungo non significa
        interrompere l'utente a ogni passo."""
        with self._lock:
            task = self._require(task_id)
            if task.status != TaskStatus.RUNNING:
                raise ValueError(f"{task_id}: non e' in esecuzione (stato {task.status.value})")
            task.last_progress_at = self._clock()
            if message:
                task.message = message

    def _transition(self, task_id: str, status: TaskStatus, message: str) -> None:
        with self._lock:
            task = self._require(task_id)
            if task.status in _TERMINAL:
                raise ValueError(f"{task_id}: gia' concluso ({task.status.value}), nessuna nuova transizione")
            now = self._clock()
            task.status, task.message, task.last_progress_at = status, message, now
            task._notified_transitions += 1
            if status in _NOTIFY_ON:
                self._pending_events.append(MonitorEvent(task_id, task.label, status, message, now))

    def complete(self, task_id: str, message: str = "completato") -> None:
        self._transition(task_id, TaskStatus.COMPLETED, message)

    def fail(self, task_id: str, message: str) -> None:
        self._transition(task_id, TaskStatus.ERROR, message)

    def flag_anomaly(self, task_id: str, message: str) -> None:
        """Un'anomalia NON e' terminale (F6.7.2 la elenca insieme a completamento/errore/decisione come motivo
        di notifica, ma un compito puo' continuare dopo un'anomalia segnalata - es. un rallentamento, non un
        blocco): il task resta `ANOMALY` finche' non arriva un'altra transizione esplicita."""
        with self._lock:
            task = self._require(task_id)
            if task.status in _TERMINAL:
                raise ValueError(f"{task_id}: gia' concluso ({task.status.value})")
            now = self._clock()
            task.status, task.message, task.last_progress_at = TaskStatus.ANOMALY, message, now
            task._notified_transitions += 1
            self._pending_events.append(MonitorEvent(task_id, task.label, TaskStatus.ANOMALY, message, now))

    def resume_progress(self, task_id: str, message: str = "") -> None:
        """Dopo un'anomalia segnalata, il compito torna a scorrere normalmente - nessun evento (il ritorno alla
        normalita' non e' una delle quattro transizioni che il criterio di uscita chiede di notificare)."""
        with self._lock:
            task = self._require(task_id)
            if task.status != TaskStatus.ANOMALY:
                raise ValueError(f"{task_id}: non e' in stato di anomalia")
            task.status, task.last_progress_at = TaskStatus.RUNNING, self._clock()
            if message:
                task.message = message

    def require_decision(self, task_id: str, message: str) -> None:
        self._transition(task_id, TaskStatus.NEEDS_DECISION, message)

    def is_stalled(self, task_id: str, stall_after_seconds: float) -> bool:
        """Vero se un compito IN ESECUZIONE non da' segni di vita da troppo tempo - il chiamante decide cosa
        farne (tipicamente `flag_anomaly`), questo modulo non lo fa da solo: un timeout non e' per forza
        un'anomalia (un compito puo' legittimamente stare fermo, es. in attesa di rete)."""
        task = self._require(task_id)
        return task.status == TaskStatus.RUNNING and (self._clock() - task.last_progress_at) >= stall_after_seconds

    def drain_events(self) -> list[MonitorEvent]:
        """Gli eventi da notificare accumulati, UNA sola volta: chiamarlo di nuovo senza nuove transizioni
        ritorna una lista vuota (l'idempotenza che il criterio di uscita chiede)."""
        with self._lock:
            events, self._pending_events = list(self._pending_events), deque()
            return events

    def get(self, task_id: str) -> MonitoredTask:
        return self._require(task_id)

    def active(self) -> list[MonitoredTask]:
        return [t for t in self._tasks.values() if t.status not in _TERMINAL]

    def close(self, task_id: str) -> None:
        """F6.7.7: rimuove un compito CONCLUSO dal registro attivo - un compito ancora `RUNNING`/`ANOMALY`/
        `NEEDS_DECISION` non si chiude (chiuderlo perderebbe la possibilita' di offrirlo alla ripresa)."""
        with self._lock:
            task = self._require(task_id)
            if task.status not in _TERMINAL:
                raise ValueError(f"{task_id}: non ancora concluso ({task.status.value}), non si chiude")
            del self._tasks[task_id]

    def snapshot(self) -> list[dict]:
        """Stato di ogni compito ANCORA in corso (non concluso), serializzabile - la base di `MonitorStore`."""
        return [{"task_id": t.task_id, "label": t.label, "status": t.status.value, "started_at": t.started_at,
                "last_progress_at": t.last_progress_at, "message": t.message}
               for t in self._tasks.values() if t.status not in _TERMINAL]


# ---- F6.7.7: sopravvivenza a un'interruzione anomala -----------------------------------------------------------------------------


class MonitorStore:
    """Persiste lo snapshot dei compiti ancora in corso, cosi' un'interruzione anomala (crash, kill switch,
    spegnimento) non fa sparire senza traccia "stavo ancora facendo X". Stesso pattern di
    `core/agent_checkpoint.py`: scrittura atomica, nessuna ripresa automatica."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_MONITOR_STORE_PATH

    def save(self, registry: TaskMonitorRegistry) -> None:
        snapshot = registry.snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": 1, "tasks": snapshot}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def resume_candidates(self) -> list[dict]:
        """I compiti trovati ancora `RUNNING`/`ANOMALY`/`NEEDS_DECISION` all'ultimo salvataggio - solo dati, MAI
        ripresi da soli (F6.7: nessuna azione autonoma su un'interruzione, la ripresa reale e' specifica del
        compito e resta a chi chiama)."""
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
        return list(data.get("tasks", []))

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


# ---- F6.7.3: scoperta di housekeeping (analisi pura, su dati dichiarati) ---------------------------------------------------------


class Category(str, Enum):
    DUPLICATE = "duplicate"
    BACKUP_STALE = "backup_stale"
    UPDATE_AVAILABLE = "update_available"
    SECURITY_ISSUE = "security_issue"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class FileRecord:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class BackupRecord:
    name: str
    last_success_at: float


@dataclass(frozen=True)
class UpdateRecord:
    name: str
    current_version: str
    latest_version: str
    security_relevant: bool = False


@dataclass(frozen=True)
class SecurityCheck:
    name: str
    passed: bool
    detail: str = ""
    severity: Severity = Severity.MEDIUM


@dataclass(frozen=True)
class Finding:
    id: str
    category: Category
    severity: Severity
    description: str
    affected_paths: tuple[str, ...] = ()
    estimated_bytes_reclaimed: int = 0


def _finding_id(category: Category, *parts: str) -> str:
    return f"{category.value}:{hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:12]}"


def find_duplicates(files: list[FileRecord]) -> list[Finding]:
    """Gruppi di file con LO STESSO hash e la STESSA dimensione (le due cose insieme: un hash troncato o una
    collisione accidentale non bastano da soli). Un gruppo di un solo file non e' un duplicato."""
    groups: dict[tuple[str, int], list[FileRecord]] = defaultdict(list)
    for record in files:
        groups[(record.sha256, record.size_bytes)].append(record)
    findings = []
    for (digest, size), group in groups.items():
        if len(group) < 2:
            continue
        paths = tuple(sorted(record.path for record in group))
        reclaimable = size * (len(group) - 1)  # se ne tiene UNA copia
        findings.append(Finding(_finding_id(Category.DUPLICATE, digest, str(size)), Category.DUPLICATE,
                                Severity.LOW, f"{len(group)} copie identiche ({size} byte l'una)", paths, reclaimable))
    return sorted(findings, key=lambda f: f.id)


def stale_backups(backups: list[BackupRecord], now: float, max_age_seconds: float) -> list[Finding]:
    findings = []
    for backup in backups:
        age = now - backup.last_success_at
        if age <= max_age_seconds:
            continue
        severity = Severity.HIGH if age > max_age_seconds * 3 else Severity.MEDIUM
        findings.append(Finding(_finding_id(Category.BACKUP_STALE, backup.name), Category.BACKUP_STALE, severity,
                                f"'{backup.name}': ultimo backup riuscito {age / 86400:.1f} giorni fa"))
    return sorted(findings, key=lambda f: f.id)


def available_updates(updates: list[UpdateRecord]) -> list[Finding]:
    findings = []
    for update in updates:
        if update.current_version == update.latest_version:
            continue
        severity = Severity.HIGH if update.security_relevant else Severity.LOW
        findings.append(Finding(_finding_id(Category.UPDATE_AVAILABLE, update.name), Category.UPDATE_AVAILABLE,
                                severity, f"'{update.name}': {update.current_version} -> {update.latest_version}"
                                + (" (sicurezza)" if update.security_relevant else "")))
    return sorted(findings, key=lambda f: f.id)


def security_posture(checks: list[SecurityCheck]) -> list[Finding]:
    findings = []
    for check in checks:
        if check.passed:
            continue
        findings.append(Finding(_finding_id(Category.SECURITY_ISSUE, check.name), Category.SECURITY_ISSUE,
                                check.severity, f"'{check.name}': {check.detail or 'controllo non superato'}"))
    return sorted(findings, key=lambda f: f.id)


def scan(*, files: list[FileRecord] | None = None, backups: list[BackupRecord] | None = None,
        updates: list[UpdateRecord] | None = None, checks: list[SecurityCheck] | None = None,
        now: float | None = None, max_backup_age_seconds: float = 7 * 86400) -> list[Finding]:
    """Scansione completa da record dichiarati. Ogni argomento e' opzionale: chi non ha dati per una categoria
    la omette, non riceve finding inventati per quella."""
    now = now if now is not None else time.time()
    return (find_duplicates(files or []) + stale_backups(backups or [], now, max_backup_age_seconds)
            + available_updates(updates or []) + security_posture(checks or []))


# ---- F6.7.4/F6.7.5: suggerimento, anteprima e azione separati --------------------------------------------------------------------


@dataclass(frozen=True)
class FileAction:
    """Un'azione su un percorso, con il suo costo DICHIARATO (mai stimato leggendo il disco qui: chi propone
    l'azione lo sa gia', avendo appena scansionato)."""

    kind: str  # "delete" | "move" | "archive"
    path: str
    bytes_impact: int
    destination: str | None = None  # per "move"/"archive"

    def __post_init__(self) -> None:
        if self.kind not in ("delete", "move", "archive"):
            raise ValueError(f"kind non valido: {self.kind}")
        if self.kind in ("move", "archive") and not self.destination:
            raise ValueError(f"{self.kind} richiede una destinazione")


@dataclass(frozen=True)
class HousekeepingPlan:
    """L'ANTEPRIMA di F6.7.4: cosa cambierebbe, mai eseguito da solo. `digest` lega un'approvazione a QUESTO
    esatto insieme di azioni - alterare il piano dopo l'approvazione invalida il digest."""

    findings: tuple[Finding, ...]
    actions: tuple[FileAction, ...]
    created_at: float

    @property
    def digest(self) -> str:
        payload = json.dumps([vars(action) for action in self.actions], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def total_bytes_impact(self) -> int:
        return sum(action.bytes_impact for action in self.actions)

    def preview(self) -> str:
        """Testo leggibile di cio' che il piano farebbe, MAI cio' che ha fatto."""
        lines = [f"Piano di housekeeping ({len(self.actions)} azioni, {self.total_bytes_impact()} byte totali):"]
        for action in self.actions:
            target = f" -> {action.destination}" if action.destination else ""
            lines.append(f"  - {action.kind} {action.path}{target} ({action.bytes_impact} byte)")
        return "\n".join(lines)


def propose(findings: list[Finding], *, delete_duplicates: bool = False, now: float | None = None) -> HousekeepingPlan:
    """Costruisce SOLO l'anteprima. `delete_duplicates=True` traduce i finding di categoria DUPLICATE in
    un'azione `delete` per ogni copia tranne la prima (ordine alfabetico, deterministico) - backup/update/
    security non producono mai un'azione automatica: quelli sono sempre e solo un suggerimento da leggere."""
    actions = []
    if delete_duplicates:
        for finding in findings:
            if finding.category != Category.DUPLICATE or len(finding.affected_paths) < 2:
                continue
            per_file = finding.estimated_bytes_reclaimed // (len(finding.affected_paths) - 1)
            for path in finding.affected_paths[1:]:  # la prima si tiene sempre
                actions.append(FileAction("delete", path, per_file))
    return HousekeepingPlan(tuple(findings), tuple(actions), now if now is not None else time.time())


@dataclass(frozen=True)
class Approval:
    digest: str
    actor: str


def approve(plan: HousekeepingPlan, actor: str) -> Approval:
    """Da chiamare SOLO da un'azione esplicita dell'utente, dopo aver mostrato `plan.preview()`."""
    if not actor.strip():
        raise ValueError("un'approvazione richiede un attore")
    return Approval(plan.digest, actor)


class HousekeepingError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def apply(plan: HousekeepingPlan, approval: Approval | None, executor: Callable[[FileAction], None],
         budget: "AutonomyBudgetTracker | None" = None) -> list[FileAction]:
    """L'UNICO punto del modulo che puo' portare a un cambiamento reale - e anche qui delega ogni singola
    azione a `executor` (iniettato dal chiamante: questo modulo non importa mai `os.remove`/`shutil`).
    F6.7.5: senza approvazione legata ESATTAMENTE a questo piano, nessuna azione parte. F6.7.6: se un budget e'
    passato, un piano che lo supererebbe e' rifiutato PRIMA di eseguire una sola azione."""
    if approval is None or approval.digest != plan.digest:
        raise HousekeepingError("approval_required", "nessuna approvazione valida per questo piano esatto")
    if budget is not None:
        exceeded = budget.would_exceed({"actions": len(plan.actions), "bytes": plan.total_bytes_impact()})
        if exceeded:
            raise HousekeepingError("budget_exceeded", f"dimensioni oltre il limite: {exceeded}")
    applied = []
    for action in plan.actions:
        executor(action)
        applied.append(action)
    if budget is not None:
        budget.record({"actions": len(plan.actions), "bytes": plan.total_bytes_impact()})
    return applied


# ---- F6.7.6: budget di autonomia multi-dimensionale -------------------------------------------------------------------------------


class AutonomyBudgetTracker:
    """Finestra scorrevole per PIU' dimensioni di costo dichiarate (byte, secondi, chiamate di rete, azioni,
    un punteggio di "impatto" - qualunque chiave il chiamante usa, non un elenco chiuso): stesso principio di
    `core/autonomy_budget.py::AutonomyBudget` (finestra scorrevole, non un contatore che si azzera a
    mezzanotte), generalizzato a piu' assi contemporaneamente invece del solo conteggio di azioni."""

    def __init__(self, limits: dict[str, float], window_seconds: float = 3600, clock: Callable[[], float] = time.time) -> None:
        self.limits = dict(limits)
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: deque[tuple[float, dict[str, float]]] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def used(self) -> dict[str, float]:
        with self._lock:
            self._prune(self._clock())
            totals: dict[str, float] = dict.fromkeys(self.limits, 0.0)
            for _, cost in self._events:
                for key, value in cost.items():
                    if key in totals:
                        totals[key] += value
            return totals

    def would_exceed(self, cost: dict[str, float]) -> list[str]:
        """Le dimensioni che SUPEREREBBERO il limite se `cost` venisse registrato ora - non registra nulla."""
        used = self.used()
        return sorted(key for key, amount in cost.items()
                     if key in self.limits and used.get(key, 0.0) + amount > self.limits[key])

    def record(self, cost: dict[str, float]) -> None:
        with self._lock:
            self._events.append((self._clock(), dict(cost)))
