"""Intelligenza delle notifiche: priorita', quiet hours, device, digest, feedback, relevance (F6.3).

`NotificationCenter` (v4.3) decide con una matrice modalita' x tipo: in modalita' studio passano solo i
promemoria, in riunione niente. E' semplice e prevedibile ma non sa distinguere un promemoria della
medicina da uno per buttare la spazzatura, non conosce un contatto critico, non sa quale dispositivo sta
usando l'utente ne' se lo speaker e' condiviso. Questo modulo aggiunge il ragionamento SOPRA, senza
toccare la matrice: `NotificationPolicy.decide` ritorna una decisione (subito / in coda / digest / scarta)
con il canale (voce o schermo), il dispositivo e la RAGIONE, sempre spiegabile.

Regole di fondo, ciascuna con un test:
- un evento CRITICO dichiarato dal produttore passa sempre (in riunione o di notte, in silenzio sullo
  schermo invece che a voce): la criticita' la dichiara chi produce la notifica, mai la si indovina dal testo
  (stesso principio "rischio dichiarato, non inferito" del resto del progetto);
- niente e' rimandato per sempre: ogni voce in coda invecchia, guadagna priorita' e a un certo punto esce
  in un digest (F6.3.7);
- una notifica di contenuto non dichiarato pubblico non si legge a voce su uno speaker condiviso (F6.3.5): a
  voce si dice solo che c'e' qualcosa, i dettagli vanno sullo schermo privato;
- il "meno notifiche come questa" e' reversibile, decade e NON puo' silenziare cio' che e' critico;
- Jake non chiama in loop: dopo un rifiuto o una mancata risposta si ricade su notifica/digest con un
  cooldown, e non esiste alcuna escalation automatica alla rete telefonica (F6.3.9)."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import time as clock_time
from pathlib import Path

from core.notification_center import NotificationMode

KIND_BASE_PRIORITY = {"reminder": 70, "trigger": 50, "advisory": 30}
# soglia di priorita' che una notifica deve raggiungere per INTERROMPERE nella modalita' data
MODE_THRESHOLD = {
    NotificationMode.NORMAL: 0, NotificationMode.GAMING: 60, NotificationMode.STUDY: 65,
    NotificationMode.DO_NOT_DISTURB: 80, NotificationMode.SLEEP: 90, NotificationMode.MEETING: 101,  # 101: nulla, salvo critico
}
SENSITIVITY_VALUES = ("public", "private", "unknown")
PRIVATE_SPEECH_PLACEHOLDER = "Hai una notifica privata: i dettagli sono sullo schermo."


@dataclass
class Notification:
    kind: str  # reminder | trigger | advisory
    message: str
    source: str = ""
    critical: bool = False
    contact: str | None = None
    sensitivity: str = "unknown"  # public | private | unknown (unknown = trattato come privato su speaker condiviso)
    target_device: str | None = None
    group: str | None = None
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.sensitivity not in SENSITIVITY_VALUES:
            raise ValueError(f"sensitivity deve essere una tra {SENSITIVITY_VALUES}")

    @property
    def feedback_key(self) -> str:
        return f"{self.kind}:{self.source}" if self.source else self.kind


@dataclass(frozen=True)
class Device:
    device_id: str
    kind: str  # pc | phone | watch | speaker
    online: bool = True
    active: bool = False  # e' il dispositivo su cui l'utente sta lavorando adesso
    shared_speaker: bool = False  # l'audio puo' essere sentito da altre persone
    private_screen: bool = True


@dataclass(frozen=True)
class Decision:
    action: str  # "deliver_now" | "queue" | "digest" | "drop"
    reason: str
    priority: float
    channel: str | None = None  # "voice" | "screen"
    device_id: str | None = None
    spoken_text: str | None = None  # cio' che si puo' dire a voce (mai piu' del consentito)
    screen_text: str | None = None


@dataclass(frozen=True)
class QuietHours:
    start: clock_time
    end: clock_time

    def contains(self, moment: clock_time) -> bool:
        if self.start <= self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end  # a cavallo di mezzanotte


class FeedbackStore:
    """"Meno notifiche come questa" (F6.3.4): un moltiplicatore per tipo+fonte che scende a ogni richiesta e
    torna verso 1 col tempo. Non riguarda mai le notifiche critiche (vedi NotificationPolicy.score)."""

    HALF_LIFE_DAYS = 30.0
    FLOOR = 0.15

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path) if path else None
        self._clock = clock
        self._entries: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if self.path and self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8")).get("entries", {})
            except (OSError, ValueError):
                return {}
        return {}

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "entries": self._entries}), encoding="utf-8")
        os.replace(tmp, self.path)

    def less_like_this(self, key: str) -> float:
        """Registra la richiesta e ritorna il nuovo moltiplicatore (dimezza ogni volta, mai sotto FLOOR)."""
        current = self.multiplier(key)
        self._entries[key] = {"multiplier": max(self.FLOOR, current * 0.5), "at": self._clock()}
        self._save()
        return self._entries[key]["multiplier"]

    def undo(self, key: str) -> bool:
        removed = self._entries.pop(key, None) is not None
        if removed:
            self._save()
        return removed

    def multiplier(self, key: str) -> float:
        entry = self._entries.get(key)
        if entry is None:
            return 1.0
        age_days = max(0.0, (self._clock() - entry["at"]) / 86400)
        recovery = 1 - 0.5 ** (age_days / self.HALF_LIFE_DAYS)  # torna verso 1 col tempo
        return entry["multiplier"] + (1 - entry["multiplier"]) * recovery


@dataclass
class _Queued:
    notification: Notification
    queued_at: float


class RelevanceTracker:
    """Misura quante interruzioni sono state inutili (F6.3.6): un'interruzione e' rilevante se l'utente ha agito,
    irrilevante se l'ha ignorata/scartata o ha chiesto "meno come questa". L'obiettivo di F6 e' < 1 irrilevante al giorno."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._events: list[tuple[float, str, bool]] = []  # (quando, chiave, rilevante)

    def record(self, notification: Notification, acted: bool) -> None:
        self._events.append((self._clock(), notification.feedback_key, acted))

    def summary(self, days: float = 1.0) -> dict:
        since = self._clock() - days * 86400
        recent = [e for e in self._events if e[0] >= since]
        irrelevant = [e for e in recent if not e[2]]
        by_source = Counter(e[1] for e in irrelevant)
        return {
            "interruptions": len(recent), "irrelevant": len(irrelevant),
            "relevance_rate": (1 - len(irrelevant) / len(recent)) if recent else None,
            "irrelevant_per_day": len(irrelevant) / days, "worst_sources": by_source.most_common(3),
        }


class NotificationPolicy:
    def __init__(
        self,
        mode: NotificationMode = NotificationMode.NORMAL,
        quiet_hours: QuietHours | None = None,
        critical_contacts: set[str] | None = None,
        vip_contacts: set[str] | None = None,
        feedback: FeedbackStore | None = None,
        clock: Callable[[], float] = time.time,
        now_of_day: Callable[[], clock_time] | None = None,
        max_wait_s: float = 3600.0,
        queue_limit: int = 50,
    ) -> None:
        self.mode = mode
        self.quiet_hours = quiet_hours
        self.critical_contacts = critical_contacts or set()
        self.vip_contacts = vip_contacts or set()
        self.feedback = feedback or FeedbackStore(clock=clock)
        self._clock = clock
        self._now_of_day = now_of_day or (lambda: clock_time(*time.localtime(clock())[3:5]))
        self.max_wait_s = max_wait_s
        self.queue_limit = queue_limit
        self._queue: list[_Queued] = []
        self.overflow: list[_Queued] = []
        self.dropped = 0  # notifiche non conservate perche' anche l'eccedenza aveva raggiunto il tetto

    OVERFLOW_LIMIT = 500

    # ---- priorita' ---------------------------------------------------------------------------------------

    def is_critical(self, notification: Notification) -> bool:
        return notification.critical or (notification.contact is not None and notification.contact in self.critical_contacts)

    def score(self, notification: Notification, now: float | None = None) -> float:
        """Priorita' 0-100. Critico = 100 sempre. Altrimenti: base per tipo, bonus contatto VIP, moltiplicatore del
        feedback ("meno come questa"), bonus per l'eta' in coda (un'ora in coda vale +10, fino a +30)."""
        if self.is_critical(notification):
            return 100.0
        value: float = KIND_BASE_PRIORITY.get(notification.kind, 30)
        if notification.contact is not None and notification.contact in self.vip_contacts:
            value += 25
        value *= self.feedback.multiplier(notification.feedback_key)
        age_hours = max(0.0, ((now or self._clock()) - notification.created_at) / 3600)
        value += min(30.0, age_hours * 10)
        return min(99.0, round(value, 1))

    def threshold(self) -> float:
        base = MODE_THRESHOLD[self.mode]
        if self.quiet_hours and self.quiet_hours.contains(self._now_of_day()):
            base = max(base, MODE_THRESHOLD[NotificationMode.SLEEP])
        return base

    # ---- decisione -----------------------------------------------------------------------------------------------

    def decide(self, notification: Notification, devices: list[Device] | None = None) -> Decision:
        now = self._clock()
        priority = self.score(notification, now)
        critical = self.is_critical(notification)
        threshold = self.threshold()
        silent_mode = self.mode in (NotificationMode.MEETING, NotificationMode.SLEEP) or self._in_quiet_hours()
        if priority < threshold and not critical:
            self._enqueue(notification, now)
            return Decision("queue", f"priorita' {priority:.0f} sotto la soglia {threshold:.0f} della modalita' {self.mode.value}", priority)
        device = self._choose_device(notification, devices)
        if device is None and devices is not None:
            self._enqueue(notification, now)
            return Decision("queue", "nessun dispositivo raggiungibile adesso", priority)
        channel = self._channel(notification, device, force_screen=critical and silent_mode)
        spoken = self._spoken_text(notification, device) if channel == "voice" else None
        reason = "evento critico" if critical else f"priorita' {priority:.0f} >= soglia {threshold:.0f}"
        if critical and silent_mode:
            reason += ": in silenzio sullo schermo per non disturbare a voce"
        return Decision("deliver_now", reason, priority, channel, device.device_id if device else None, spoken, notification.message)

    def in_quiet_hours(self) -> bool:
        return self._in_quiet_hours()

    def _in_quiet_hours(self) -> bool:
        return bool(self.quiet_hours and self.quiet_hours.contains(self._now_of_day()))

    def _enqueue(self, notification: Notification, now: float) -> None:
        self._queue.append(_Queued(notification, now))
        if len(self._queue) > self.queue_limit:
            # il limite protegge la memoria: si tengono le piu' importanti, le altre finiscono comunque nel digest
            self._queue.sort(key=lambda q: self.score(q.notification, now), reverse=True)
            self.overflow.extend(self._queue[self.queue_limit:])
            self._queue = self._queue[:self.queue_limit]
            if len(self.overflow) > self.OVERFLOW_LIMIT:  # anche l'eccedenza ha un tetto: la memoria non cresce senza fine
                excess = len(self.overflow) - self.OVERFLOW_LIMIT
                self.overflow = self.overflow[excess:]
                self.dropped += excess

    # ---- dispositivo e canale (F6.3.2, F6.3.5) -----------------------------------------------------------------------

    def _choose_device(self, notification: Notification, devices: list[Device] | None) -> Device | None:
        if devices is None:
            return None  # nessuna informazione sui dispositivi: si decide solo il canale
        online = [d for d in devices if d.online]
        if notification.target_device is not None:
            targeted = [d for d in online if d.device_id == notification.target_device]
            return targeted[0] if targeted else None  # un target dichiarato non offline si sostituisce di nascosto
        # contenuto non pubblico: solo dispositivi con schermo privato
        candidates = online if notification.sensitivity == "public" else [d for d in online if d.private_screen]
        for preferred in (lambda d: d.active, lambda d: d.kind == "phone", lambda d: True):
            found = [d for d in candidates if preferred(d)]
            if found:
                return found[0]
        return None

    def _channel(self, notification: Notification, device: Device | None, force_screen: bool) -> str:
        if force_screen or self.mode in (NotificationMode.MEETING, NotificationMode.SLEEP):
            return "screen"
        if device is not None and device.kind == "watch":
            return "screen"
        return "voice"

    @staticmethod
    def _spoken_text(notification: Notification, device: Device | None) -> str:
        """A voce, mai piu' di quanto sia sicuro: su uno speaker condiviso un contenuto non dichiarato pubblico si
        annuncia in modo generico (F6.3.5)."""
        shared = device.shared_speaker if device is not None else False
        if shared and notification.sensitivity != "public":
            return PRIVATE_SPEECH_PLACEHOLDER
        return notification.message

    # ---- coda, digest, starvation (F6.3.3, F6.3.7) ------------------------------------------------------------------------

    def pending(self) -> int:
        return len(self._queue)

    def due_for_release(self) -> list[Notification]:
        """Le voci che hanno aspettato piu' di `max_wait_s`: NON possono restare in coda per sempre, escono comunque
        (in un digest) anche se la modalita' e' ancora restrittiva - a meno che siano in riunione, dove escono alla fine."""
        now = self._clock()
        if self.mode == NotificationMode.MEETING:
            return []
        return [q.notification for q in self._queue if now - q.queued_at >= self.max_wait_s]

    def release(self, only_due: bool = False) -> Digest | None:
        """Svuota la coda (tutta o solo le scadute) in un digest raggruppato."""
        now = self._clock()
        if only_due:
            due_ids = {id(n) for n in self.due_for_release()}
            taken = [q for q in self._queue if id(q.notification) in due_ids]
            self._queue = [q for q in self._queue if id(q.notification) not in due_ids]
        else:
            taken, self._queue = self._queue, []
        taken.extend(self.overflow)
        self.overflow = []
        dropped, self.dropped = self.dropped, 0
        if not taken:
            return None
        return build_digest([q.notification for q in taken], self, now, dropped=dropped)

    def set_mode(self, mode: NotificationMode) -> Digest | None:
        """Cambia modalita'; uscendo da una piu' restrittiva rilascia in un digest cio' che ora supera la soglia."""
        self.mode = mode
        now, threshold = self._clock(), self.threshold()
        ready = [q for q in self._queue if self.score(q.notification, now) >= threshold]
        if not ready:
            return None
        self._queue = [q for q in self._queue if q not in ready]
        return build_digest([q.notification for q in ready], self, now)


@dataclass(frozen=True)
class Digest:
    text: str
    notifications: tuple[Notification, ...]
    counts: dict


def build_digest(notifications: list[Notification], policy: NotificationPolicy, now: float, dropped: int = 0) -> Digest:
    """Un solo messaggio per un gruppo: conta per tipo, dice le piu' importanti per prime, non ripete i doppioni."""
    ordered = sorted(notifications, key=lambda n: policy.score(n, now), reverse=True)
    seen: set[tuple[str, str]] = set()
    unique = []
    for n in ordered:
        key = (n.kind, n.message)
        if key not in seen:
            seen.add(key)
            unique.append(n)
    labels = {"reminder": "promemoria", "advisory": "avvisi di sistema", "trigger": "automazioni"}
    counts = Counter(n.kind for n in unique)
    parts = [f"{count} {labels.get(kind, kind)}" for kind, count in counts.most_common()]
    top = "; ".join(n.message for n in unique[:3])
    more = f" (e altre {len(unique) - 3})" if len(unique) > 3 else ""
    duplicates = len(ordered) - len(unique)
    tail = f" ({duplicates} ripetute non contate)" if duplicates else ""
    if dropped:
        tail += f" Altre {dropped} notifiche meno importanti non sono state conservate."
    text = f"Mentre non c'eri: {', '.join(parts)}. In primo piano: {top}{more}.{tail}"
    return Digest(text, tuple(unique), dict(counts))


# ---- contatto proattivo (F6.3.8, F6.3.9) ------------------------------------------------------------------------------------------

PSTN_ESCALATION_ALLOWED = False  # nessuna escalation automatica alla rete telefonica: mai


@dataclass(frozen=True)
class ContactPlan:
    channel: str  # "call" | "notification" | "digest" | "none"
    device_id: str | None
    reason: str
    cooldown_until: float | None = None
    briefing: str | None = None


@dataclass
class ContactPreferences:
    allow_calls: bool = False
    call_cooldown_s: float = 1800.0
    notification_cooldown_s: float = 300.0
    max_call_attempts: int = 1


class ProactiveContact:
    """Decide COME contattare l'utente (companion autorizzato) quando Jake ha qualcosa di urgente da dire. Solo
    decisione: la chiamata o la notifica vere richiedono il companion (F7.2/F7.3)."""

    def __init__(self, policy: NotificationPolicy, preferences: ContactPreferences | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.policy = policy
        self.preferences = preferences or ContactPreferences()
        self._clock = clock
        self._last_call: dict[str, float] = {}
        self._last_notification: dict[str, float] = {}
        self._failed_calls: Counter[str] = Counter()

    def plan(self, task_id: str, situation: str, urgency: str, verified_actions: list[str], decision_needed: str,
             devices: list[Device], sensitivity: str = "unknown") -> ContactPlan:
        """`urgency`: "low" | "normal" | "urgent". Un briefing sempre completo: situazione, azioni gia' verificate e
        decisione richiesta, correlato al task."""
        now = self._clock()
        briefing = (f"[task {task_id}] {situation} Gia' fatto e verificato: "
                    f"{', '.join(verified_actions) if verified_actions else 'niente'}. Serve da te: {decision_needed}")
        online = [d for d in devices if d.online]
        if urgency == "low":
            return ContactPlan("digest", None, "urgenza bassa: entra nel digest", briefing=briefing)
        if not online:
            return ContactPlan("digest", None, "nessun dispositivo online: si aspetta il prossimo digest", briefing=briefing)
        device = next((d for d in online if d.active), online[0])
        if urgency == "urgent" and self._may_call(task_id, device, now):
            self._last_call[task_id] = now
            return ContactPlan("call", device.device_id, "urgente, chiamata consentita e nessun cooldown attivo",
                               now + self.preferences.call_cooldown_s, briefing)
        cooldown = self._last_notification.get(task_id)
        if cooldown is not None and now - cooldown < self.preferences.notification_cooldown_s:
            return ContactPlan("none", None, "notifica gia' inviata da poco per questo task: nessuna insistenza",
                               cooldown + self.preferences.notification_cooldown_s, briefing)
        self._last_notification[task_id] = now
        notification = Notification("reminder", briefing, source=f"task:{task_id}", critical=(urgency == "urgent"),
                                    sensitivity=sensitivity)
        decision = self.policy.decide(notification, devices)
        if decision.action == "deliver_now":
            return ContactPlan("notification", decision.device_id, f"chiamata non consentita o non possibile; {decision.reason}",
                               now + self.preferences.notification_cooldown_s, briefing)
        return ContactPlan("digest", None, f"la modalita' attuale non consente di interrompere ({decision.reason})", briefing=briefing)

    def _may_call(self, task_id: str, device: Device, now: float) -> bool:
        if not self.preferences.allow_calls or device.kind not in ("phone", "pc"):
            return False
        if self._failed_calls[task_id] >= self.preferences.max_call_attempts:
            return False
        last = self._last_call.get(task_id)
        return last is None or now - last >= self.preferences.call_cooldown_s

    def record_call_outcome(self, task_id: str, outcome: str) -> ContactPlan:
        """Esito di una chiamata: "answered" | "rejected" | "no_answer" | "device_offline". Dopo un fallimento si ricade
        SEMPRE su notifica/digest con cooldown; mai un secondo tentativo a raffica e mai PSTN."""
        now = self._clock()
        if outcome == "answered":
            self._failed_calls.pop(task_id, None)
            return ContactPlan("none", None, "l'utente ha risposto")
        self._failed_calls[task_id] += 1
        return ContactPlan(
            "notification", None,
            f"chiamata {outcome}: si ricade su una notifica con cooldown, senza richiamare"
            + ("" if not PSTN_ESCALATION_ALLOWED else " (PSTN)"),
            now + self.preferences.call_cooldown_s,
        )


__all__ = [
    "Notification", "Device", "Decision", "QuietHours", "FeedbackStore", "RelevanceTracker", "NotificationPolicy",
    "Digest", "build_digest", "ProactiveContact", "ContactPlan", "ContactPreferences", "PSTN_ESCALATION_ALLOWED",
]
