"""Sincronizzazione cifrata con conflitti deterministici e coda offline limitata (F7.6.1, F7.6.3, F7.6.4,
F7.6.5, F7.6.6, F7.6.7). Le buste e le chiavi sono in `core/sync_crypto.py`.

**Cosa si sincronizza** (F7.6.1): configurazione (solo una lista di chiavi ammesse), conversazione, memoria e
task. Mai segreti grezzi: password amministrativa, token, ricordi con sensibilita' `secret` e campi con nomi
da segreto vengono rifiutati PRIMA di essere cifrati (`NotSyncable`), non "cifrati e sperare".

**Conflitti** (F7.6.3): ogni modifica porta un timestamp logico ibrido (`Stamp`: ora, contatore, id
dispositivo) che e' un ordine TOTALE. Lo stato e' una mappa "l'ultimo scrittore vince" PER CAMPO con lapidi per
le cancellazioni: applicare le stesse modifiche in qualunque ordine, o piu' volte, da' lo stesso stato
(test con tutte le permutazioni). Due dispositivi che modificano campi diversi dello stesso ricordo li
conservano entrambi; sullo stesso campo vince l'ultimo, e il valore PERSO viene registrato in un elenco di
conflitti consultabile: una modifica non sparisce senza traccia.

**Profili e proprietari** (F7.6.4): ogni modifica dichiara il profilo; un dispositivo puo' scrivere e ricevere
solo i profili per cui e' autorizzato nel portachiavi.

**Revoca e wipe** (F7.6.5, F7.6.6): un dispositivo revocato non e' piu' tra i destinatari, quindi non puo'
leggere nulla di nuovo, e i suoi messaggi sono rifiutati. Un comando di wipe cancella le sole chiavi e i dati di
Jake dal dispositivo perso quando questo torna online; se non torna mai, la revoca basta per il futuro.

**Coda offline** (F7.6.7): per destinatario, con limite di messaggi, di byte e di eta'. Quando qualcosa viene
scartato per il limite il destinatario e' segnato `needs_full_resync`: al suo ritorno riceve lo STATO
completo invece di una storia con dei buchi."""
from __future__ import annotations

import itertools
import json
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.sync_crypto import (
    DeviceKeys, Envelope, Keyring, Peer, SyncCryptoError, open_envelope, seal,
)

SYNCABLE_ENTITIES = {"config", "conversation", "memory", "todo"}
SYNCABLE_CONFIG_KEYS = {"follow_up_seconds", "voice_style", "hud_mode", "hud_backdrop", "notification_mode", "language"}
_SECRET_NAME_PARTS = ("password", "passphrase", "token", "secret", "credential", "api_key", "apikey", "private_key")


class NotSyncable(Exception):
    """Questo dato non si sincronizza mai."""


@dataclass(frozen=True, order=True)
class Stamp:
    wall_ms: int
    counter: int
    device: str

    def to_list(self) -> list:
        return [self.wall_ms, self.counter, self.device]

    @classmethod
    def from_list(cls, data: list) -> Stamp:
        return cls(int(data[0]), int(data[1]), str(data[2]))


ZERO_STAMP = Stamp(0, 0, "")


class HybridClock:
    """Timestamp logico ibrido: segue l'orologio reale ma non torna mai indietro e supera sempre cio' che ha visto."""

    def __init__(self, device_id: str, wall: Callable[[], float] = time.time) -> None:
        self._device = device_id
        self._wall = wall
        self._last = Stamp(0, 0, device_id)

    def now(self) -> Stamp:
        wall_ms = int(self._wall() * 1000)
        if wall_ms > self._last.wall_ms:
            self._last = Stamp(wall_ms, 0, self._device)
        else:
            self._last = Stamp(self._last.wall_ms, self._last.counter + 1, self._device)
        return self._last

    def observe(self, stamp: Stamp) -> None:
        if (stamp.wall_ms, stamp.counter) > (self._last.wall_ms, self._last.counter):
            self._last = Stamp(stamp.wall_ms, stamp.counter, self._device)


@dataclass(frozen=True)
class Change:
    id: str
    entity: str
    key: str
    op: str  # "upsert" | "delete"
    fields: dict
    stamp: Stamp
    profile: str

    def to_dict(self) -> dict:
        return {"id": self.id, "entity": self.entity, "key": self.key, "op": self.op, "fields": self.fields,
                "stamp": self.stamp.to_list(), "profile": self.profile}

    @classmethod
    def from_dict(cls, data: dict) -> Change:
        try:
            return cls(str(data["id"]), str(data["entity"]), str(data["key"]), str(data["op"]),
                       dict(data.get("fields") or {}), Stamp.from_list(data["stamp"]), str(data["profile"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SyncCryptoError("modifica malformata") from exc


def check_syncable(change: Change) -> None:
    """Rifiuta cio' che non deve mai lasciare il dispositivo (F7.6.1). Chiamata prima di cifrare E alla ricezione."""
    if change.entity not in SYNCABLE_ENTITIES:
        raise NotSyncable(f"tipo non sincronizzabile: {change.entity!r}")
    if change.entity == "config" and change.key not in SYNCABLE_CONFIG_KEYS:
        raise NotSyncable(f"la chiave di configurazione {change.key!r} non e' nell'elenco ammesso")
    if change.entity == "memory" and change.fields.get("sensitivity") == "secret":
        raise NotSyncable("i ricordi con sensibilita' 'secret' non si sincronizzano")
    for name in change.fields:
        if any(part in name.lower() for part in _SECRET_NAME_PARTS):
            raise NotSyncable(f"il campo {name!r} ha un nome da segreto: non si sincronizza")
    if change.entity == "config" and any(part in change.key.lower() for part in _SECRET_NAME_PARTS):
        raise NotSyncable("le credenziali non si sincronizzano")


@dataclass(frozen=True)
class ConflictRecord:
    entity: str
    key: str
    field: str
    lost_value: object
    lost_stamp: Stamp
    winner_stamp: Stamp


class ReplicaState:
    """Mappa LWW per campo con lapidi. Commutativa e idempotente: l'ordine e la ripetizione delle modifiche non contano."""

    def __init__(self) -> None:
        # (profilo, entity, key) -> {campo: (valore, stamp)}
        self._entities: dict[tuple[str, str, str], dict[str, tuple[object, Stamp]]] = {}
        self._deleted: dict[tuple[str, str, str], Stamp] = {}  # stamp dell'ultima cancellazione
        self.conflicts: list[ConflictRecord] = []

    def apply(self, change: Change) -> None:
        entity_key = (change.profile, change.entity, change.key)
        if change.op == "delete":
            if change.stamp > self._deleted.get(entity_key, ZERO_STAMP):
                self._deleted[entity_key] = change.stamp
            return
        fields = self._entities.setdefault(entity_key, {})
        for name, value in change.fields.items():
            current = fields.get(name)
            if current is None or change.stamp > current[1]:
                if current is not None and current[0] != value:
                    self.conflicts.append(ConflictRecord(change.entity, change.key, name, current[0], current[1], change.stamp))
                fields[name] = (value, change.stamp)
            elif change.stamp < current[1] and current[0] != value:
                self.conflicts.append(ConflictRecord(change.entity, change.key, name, value, change.stamp, current[1]))

    def get(self, profile: str, entity: str, key: str) -> dict | None:
        """I campi visibili: solo quelli scritti DOPO l'ultima cancellazione. None se non ce n'e' nessuno."""
        deleted = self._deleted.get((profile, entity, key), ZERO_STAMP)
        visible = {name: value for name, (value, stamp) in self._entities.get((profile, entity, key), {}).items() if stamp > deleted}
        return visible or None

    def snapshot(self) -> dict:
        """Stato completo in forma canonica, per confrontare due repliche."""
        out = {}
        for profile, entity, key in sorted(self._entities):
            value = self.get(profile, entity, key)
            if value is not None:
                out[f"{profile}|{entity}|{key}"] = value
        return out

    def all_changes(self, profile: str | None = None) -> list[Change]:
        """Lo stato come modifiche (per una risincronizzazione completa): campi vivi e lapidi."""
        changes = []
        for (p, e, key), fields in self._entities.items():
            if profile is not None and p != profile:
                continue
            deleted = self._deleted.get((p, e, key), ZERO_STAMP)
            for name, (value, stamp) in fields.items():
                if stamp > deleted:
                    changes.append(Change(f"snap-{uuid.uuid4().hex[:12]}", e, key, "upsert", {name: value}, stamp, p))
        for (p, e, key), stamp in self._deleted.items():
            if profile is None or p == profile:
                changes.append(Change(f"snap-{uuid.uuid4().hex[:12]}", e, key, "delete", {}, stamp, p))
        return changes

    def clear(self) -> None:
        self._entities.clear()
        self._deleted.clear()
        self.conflicts.clear()


@dataclass
class _Queued:
    envelope: Envelope
    queued_at: float
    size: int


class OutboundQueue:
    """Coda per destinatario con limiti di numero, di byte e di eta' (F7.6.7)."""

    def __init__(self, max_items: int = 500, max_bytes: int = 1_000_000, max_age_s: float = 7 * 86400,
                 clock: Callable[[], float] = time.time) -> None:
        self.max_items, self.max_bytes, self.max_age_s = max_items, max_bytes, max_age_s
        self._clock = clock
        self._queues: dict[str, deque[_Queued]] = {}
        self.needs_full_resync: set[str] = set()
        self.dropped = 0

    def push(self, envelope: Envelope) -> None:
        queue = self._queues.setdefault(envelope.recipient, deque())
        queue.append(_Queued(envelope, self._clock(), envelope.size()))
        self._enforce(envelope.recipient)

    def _enforce(self, recipient: str) -> None:
        queue = self._queues[recipient]
        cutoff = self._clock() - self.max_age_s
        while queue and queue[0].queued_at < cutoff:
            queue.popleft()
            self._lost(recipient)
        while queue and (len(queue) > self.max_items or sum(q.size for q in queue) > self.max_bytes):
            queue.popleft()
            self._lost(recipient)

    def _lost(self, recipient: str) -> None:
        self.dropped += 1
        self.needs_full_resync.add(recipient)  # con dei buchi nella storia, al ritorno serve lo stato intero

    def pending(self, recipient: str) -> int:
        if recipient in self._queues:
            self._enforce(recipient)
        return len(self._queues.get(recipient, ()))

    def drain(self, recipient: str) -> list[Envelope]:
        if recipient in self._queues:
            self._enforce(recipient)
        items = self._queues.pop(recipient, deque())
        return [q.envelope for q in items]

    def discard_for(self, recipient: str) -> int:
        """Butta tutto cio' che aspettava un destinatario (es. dopo la revoca)."""
        return len(self._queues.pop(recipient, ()))


@dataclass
class ReceiveResult:
    applied: int = 0
    duplicates: int = 0
    rejected: list[str] = field(default_factory=list)
    wiped: bool = False
    revoked: list[str] = field(default_factory=list)


class SyncNode:
    def __init__(self, device_id: str, keys: DeviceKeys, keyring: Keyring, profiles: set[str],
                 queue: OutboundQueue | None = None, wall: Callable[[], float] = time.time,
                 key_path: Path | None = None) -> None:
        self.device_id = device_id
        self.key_path = key_path  # dove sono salvate le chiavi di QUESTO dispositivo (per il wipe)
        self.keys = keys
        self.keyring = keyring
        self.profiles = set(profiles)
        self.state = ReplicaState()
        self.clock = HybridClock(device_id, wall)
        self.queue = queue or OutboundQueue(clock=wall)
        self._seen: set[str] = set()
        self._seq = itertools.count(1)
        self.wiped = False

    # ---- produrre modifiche -----------------------------------------------------------------------------------------

    def local_change(self, entity: str, key: str, op: str, fields: dict | None, profile: str) -> Change:
        if profile not in self.profiles:
            raise NotSyncable(f"questo dispositivo non e' autorizzato al profilo {profile!r}")
        change = Change(uuid.uuid4().hex, entity, key, op, dict(fields or {}), self.clock.now(), profile)
        check_syncable(change)  # PRIMA di toccare lo stato e di cifrare: un segreto non entra nemmeno nel log
        self.state.apply(change)
        self._seen.add(change.id)
        self._broadcast([change], profile)
        return change

    def _recipients(self, profile: str) -> list[Peer]:
        return [p for p in self.keyring.active() if profile in p.profiles]

    def _broadcast(self, changes: list[Change], profile: str) -> None:
        payload = json.dumps({"kind": "changes", "changes": [c.to_dict() for c in changes]}).encode("utf-8")
        for peer in self._recipients(profile):
            self.queue.push(seal(payload, self.device_id, self.keys, peer, profile, next(self._seq)))

    def snapshot_for(self, peer_id: str) -> list[Envelope]:
        """Lo stato completo per un dispositivo che ha perso la storia (coda scartata, nuovo dispositivo)."""
        peer = self.keyring.get(peer_id)
        if peer is None or peer.status != "active":
            raise SyncCryptoError("dispositivo sconosciuto o revocato: nessuno snapshot")
        envelopes = []
        for profile in sorted(peer.profiles & self.profiles):
            changes = [c for c in self.state.all_changes(profile) if self._syncable(c)]
            for start in range(0, len(changes), 100):  # a blocchi: una busta non deve diventare enorme
                payload = json.dumps({"kind": "changes", "changes": [c.to_dict() for c in changes[start:start + 100]]}).encode("utf-8")
                envelopes.append(seal(payload, self.device_id, self.keys, peer, profile, next(self._seq)))
        self.queue.needs_full_resync.discard(peer_id)
        return envelopes

    @staticmethod
    def _syncable(change: Change) -> bool:
        try:
            check_syncable(change)
            return True
        except NotSyncable:
            return False

    # ---- ricevere ------------------------------------------------------------------------------------------------------------

    def receive(self, envelope: Envelope) -> ReceiveResult:
        result = ReceiveResult()
        if self.wiped:
            result.rejected.append("dispositivo cancellato (wipe): nessun messaggio accettato")
            return result
        try:
            plaintext = open_envelope(envelope, self.device_id, self.keys, self.keyring)
        except SyncCryptoError as exc:
            result.rejected.append(str(exc))
            return result
        sender = self.keyring.get(envelope.sender)
        assert sender is not None  # open_envelope ha gia' verificato mittente attivo
        try:
            message = json.loads(plaintext)
        except ValueError:
            result.rejected.append("contenuto non valido")
            return result
        kind = message.get("kind")
        if kind == "wipe":
            self.wipe()
            result.wiped = True
            return result
        if kind == "revoke":
            target = str(message.get("device", ""))
            if target and target != self.device_id and self.keyring.revoke(target):
                self.queue.discard_for(target)
                result.revoked.append(target)
            return result
        if kind != "changes":
            result.rejected.append(f"tipo di messaggio sconosciuto: {kind!r}")
            return result
        for raw in message.get("changes", []):
            try:
                change = Change.from_dict(raw)
                check_syncable(change)
            except (SyncCryptoError, NotSyncable) as exc:
                result.rejected.append(str(exc))
                continue
            if change.profile != envelope.profile or change.profile not in sender.profiles or change.profile not in self.profiles:
                result.rejected.append(f"profilo {change.profile!r} non autorizzato per {envelope.sender}")
                continue
            if change.id in self._seen:
                result.duplicates += 1
                continue
            self._seen.add(change.id)
            self.clock.observe(change.stamp)
            self.state.apply(change)
            result.applied += 1
        return result

    # ---- revoca e wipe (F7.6.5, F7.6.6) ------------------------------------------------------------------------------------------

    def request_wipe(self, peer_id: str) -> Envelope:
        """Mette in coda il comando di wipe per un dispositivo perso. Va creato PRIMA della revoca (dopo, non si cifra piu'
        per lui); alla prima volta che il dispositivo torna online lo esegue."""
        peer = self.keyring.get(peer_id)
        if peer is None or peer.status != "active":
            raise SyncCryptoError("il wipe si richiede prima della revoca, per un dispositivo noto")
        profile = sorted(peer.profiles)[0] if peer.profiles else "-"
        envelope = seal(json.dumps({"kind": "wipe"}).encode(), self.device_id, self.keys, peer, profile, next(self._seq))
        self.queue.push(envelope)
        return envelope

    def revoke_device(self, peer_id: str) -> bool:
        """Revoca: il dispositivo non e' piu' destinatario di nulla di nuovo, i suoi messaggi sono rifiutati e gli altri
        dispositivi vengono avvisati con un messaggio di revoca. Il wipe eventualmente gia' in coda RESTA in coda."""
        if not self.keyring.revoke(peer_id):
            return False
        payload = json.dumps({"kind": "revoke", "device": peer_id}).encode("utf-8")
        for peer in self.keyring.active():
            self.queue.push(seal(payload, self.device_id, self.keys, peer, "-", next(self._seq)))
        return True

    def wipe(self) -> None:
        """Cancella SOLO cio' che e' di Jake su questo dispositivo: chiavi, portachiavi, stato replicato, coda."""
        self.keys.wipe(self.key_path)
        self.keyring.wipe()
        self.state.clear()
        self.queue = OutboundQueue(clock=time.time)
        self._seen.clear()
        self.wiped = True
