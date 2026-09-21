"""Chiavi di dispositivo e buste cifrate end-to-end per la sincronizzazione (F7.6.2, F7.6.5, F7.6.6).

Ogni dispositivo ha DUE coppie di chiavi, mai riusate una per l'altra scopo:
- Ed25519 per FIRMARE cio' che manda (chi ha scritto questo messaggio?);
- X25519 per CIFRARE cio' che riceve (chi puo' leggerlo?).

Una busta e' cifrata per UN destinatario: si genera una chiave X25519 effimera, si deriva un segreto
condiviso con la chiave pubblica di accordo del destinatario, HKDF-SHA256 (con mittente e destinatario nel
contesto) ne ricava una chiave e ChaCha20-Poly1305 cifra e autentica. L'intestazione (mittente,
destinatario, profilo, numero di sequenza) viaggia in chiaro ma e' AUTENTICATA e FIRMATA: cambiarla fa
fallire la verifica. Un relay (il companion server, un cloud) vede solo intestazione e byte cifrati.

Conseguenza che il criterio di F7.6 chiede ("un dispositivo revocato non legge nuovi dati"): i dati nuovi
si cifrano per i dispositivi ATTIVI del portachiavi; un dispositivo revocato non e' piu' tra i
destinatari, quindi non ha la chiave per nulla di cio' che viene dopo - senza bisogno che il dispositivo
perso collabori. Cio' che ha gia' ricevuto prima della revoca non si puo' "ritirare": lo dichiara la
documentazione e per questo esiste il wipe (`DeviceKeys.wipe`) per le sole chiavi di Jake.

Nessuna primitiva scritta a mano: solo `cryptography`."""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENVELOPE_VERSION = 1


class SyncCryptoError(Exception):
    """Busta non valida, non per noi, di un mittente non fidato o manomessa."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _raw_public(key) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


@dataclass(frozen=True)
class PublicKeys:
    sign: bytes  # Ed25519
    agree: bytes  # X25519

    def to_dict(self) -> dict:
        return {"sign": _b64(self.sign), "agree": _b64(self.agree)}

    @classmethod
    def from_dict(cls, data: dict) -> PublicKeys:
        sign, agree = _unb64(data["sign"]), _unb64(data["agree"])
        if len(sign) != 32 or len(agree) != 32:
            raise SyncCryptoError("chiave pubblica di lunghezza non valida")
        return cls(sign, agree)


class DeviceKeys:
    """Le chiavi private di QUESTO dispositivo. Il file su disco puo' essere protetto da un `vault` (DPAPI:
    `core/secrets_vault.py`) con `protect(str) -> str` / `unprotect(str) -> str|None`."""

    def __init__(self, signing: Ed25519PrivateKey, agreement: X25519PrivateKey) -> None:
        self._signing = signing
        self._agreement = agreement
        self.wiped = False

    @classmethod
    def generate(cls) -> DeviceKeys:
        return cls(Ed25519PrivateKey.generate(), X25519PrivateKey.generate())

    @property
    def public(self) -> PublicKeys:
        self._check()
        return PublicKeys(_raw_public(self._signing.public_key()), _raw_public(self._agreement.public_key()))

    def _check(self) -> None:
        if self.wiped:
            raise SyncCryptoError("le chiavi di questo dispositivo sono state cancellate (wipe)")

    def sign(self, data: bytes) -> bytes:
        self._check()
        return self._signing.sign(data)

    def agree_with(self, peer_public: bytes) -> bytes:
        self._check()
        return self._agreement.exchange(X25519PublicKey.from_public_bytes(peer_public))

    # ---- persistenza e wipe -------------------------------------------------------------------------------------

    def save(self, path: Path, vault=None) -> None:
        self._check()
        raw = json.dumps({
            "sign": _b64(self._signing.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())),
            "agree": _b64(self._agreement.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())),
        })
        payload = vault.protect(raw) if vault is not None else raw
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path, vault=None) -> DeviceKeys:
        text = Path(path).read_text(encoding="utf-8")
        if vault is not None:
            text = vault.unprotect(text)
            if text is None:
                raise SyncCryptoError("impossibile decifrare il file delle chiavi (altro utente o altra macchina)")
        data = json.loads(text)
        return cls(Ed25519PrivateKey.from_private_bytes(_unb64(data["sign"])), X25519PrivateKey.from_private_bytes(_unb64(data["agree"])))

    def wipe(self, path: Path | None = None) -> None:
        """Cancella le chiavi di Jake da questo dispositivo: dalla memoria (le chiavi non sono piu' usabili) e dal
        file. Non tocca nient'altro."""
        self.wiped = True
        self._signing = None  # type: ignore[assignment]
        self._agreement = None  # type: ignore[assignment]
        if path is not None:
            Path(path).unlink(missing_ok=True)


@dataclass
class Peer:
    device_id: str
    keys: PublicKeys
    profiles: set[str] = field(default_factory=set)  # profili che questo dispositivo puo' ricevere/scrivere
    status: str = "active"  # "active" | "revoked"
    revoked_at: float | None = None


class Keyring:
    """I dispositivi noti e il loro stato. `revoke` toglie il dispositivo dai destinatari futuri."""

    def __init__(self, clock=time.time) -> None:
        self._peers: dict[str, Peer] = {}
        self._clock = clock

    def add(self, device_id: str, keys: PublicKeys, profiles: set[str] | None = None) -> Peer:
        existing = self._peers.get(device_id)
        if existing is not None and existing.status == "revoked":
            raise SyncCryptoError(f"{device_id} e' stato revocato: serve un nuovo pairing con un nuovo id, non si riattiva")
        peer = Peer(device_id, keys, set(profiles or ()))
        self._peers[device_id] = peer
        return peer

    def revoke(self, device_id: str) -> bool:
        peer = self._peers.get(device_id)
        if peer is None or peer.status == "revoked":
            return False
        peer.status, peer.revoked_at = "revoked", self._clock()
        return True

    def get(self, device_id: str) -> Peer | None:
        return self._peers.get(device_id)

    def active(self) -> list[Peer]:
        return [p for p in self._peers.values() if p.status == "active"]

    def is_active(self, device_id: str) -> bool:
        peer = self._peers.get(device_id)
        return peer is not None and peer.status == "active"

    def wipe(self) -> None:
        self._peers.clear()


@dataclass(frozen=True)
class Envelope:
    sender: str
    recipient: str
    profile: str
    seq: int
    ephemeral: bytes
    nonce: bytes
    ciphertext: bytes
    signature: bytes

    def header(self) -> dict:
        return {"v": ENVELOPE_VERSION, "sender": self.sender, "recipient": self.recipient, "profile": self.profile,
                "seq": self.seq, "ephemeral": _b64(self.ephemeral), "nonce": _b64(self.nonce)}

    def to_dict(self) -> dict:
        return {**self.header(), "ciphertext": _b64(self.ciphertext), "signature": _b64(self.signature)}

    def size(self) -> int:
        return len(json.dumps(self.to_dict()))

    @classmethod
    def from_dict(cls, data: dict) -> Envelope:
        try:
            if data["v"] != ENVELOPE_VERSION:
                raise SyncCryptoError(f"versione di busta non supportata: {data['v']!r}")
            return cls(str(data["sender"]), str(data["recipient"]), str(data["profile"]), int(data["seq"]),
                       _unb64(data["ephemeral"]), _unb64(data["nonce"]), _unb64(data["ciphertext"]), _unb64(data["signature"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise SyncCryptoError("busta malformata") from exc


def _header_bytes(header: dict) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _content_key(shared: bytes, sender: str, recipient: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"jake-sync-v1|" + sender.encode() + b"|" + recipient.encode()).derive(shared)


def seal(plaintext: bytes, my_id: str, my_keys: DeviceKeys, recipient: Peer, profile: str, seq: int) -> Envelope:
    """Cifra `plaintext` per UN destinatario e firma la busta. Rifiuta un destinatario revocato: un dato nuovo non
    deve mai essere cifrato per chi non e' piu' autorizzato a leggerlo."""
    if recipient.status != "active":
        raise SyncCryptoError(f"{recipient.device_id} e' revocato: non si cifra per lui")
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = _raw_public(ephemeral.public_key())
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient.keys.agree))
    nonce = os.urandom(12)
    partial = Envelope(my_id, recipient.device_id, profile, seq, ephemeral_public, nonce, b"", b"")
    aad = _header_bytes(partial.header())
    ciphertext = ChaCha20Poly1305(_content_key(shared, my_id, recipient.device_id)).encrypt(nonce, plaintext, aad)
    unsigned = Envelope(my_id, recipient.device_id, profile, seq, ephemeral_public, nonce, ciphertext, b"")
    signature = my_keys.sign(aad + ciphertext)
    return Envelope(my_id, recipient.device_id, profile, seq, ephemeral_public, nonce, unsigned.ciphertext, signature)


def open_envelope(envelope: Envelope, my_id: str, my_keys: DeviceKeys, keyring: Keyring) -> bytes:
    """Verifica firma e mittente e decifra. Solleva `SyncCryptoError` se: non e' per noi, il mittente e' sconosciuto o
    revocato, la firma non torna, o la cifratura e' stata manomessa."""
    if envelope.recipient != my_id:
        raise SyncCryptoError("busta destinata a un altro dispositivo")
    sender = keyring.get(envelope.sender)
    if sender is None:
        raise SyncCryptoError("mittente sconosciuto")
    if sender.status != "active":
        raise SyncCryptoError("mittente revocato: messaggio rifiutato")
    aad = _header_bytes(envelope.header())
    try:
        Ed25519PublicKey.from_public_bytes(sender.keys.sign).verify(envelope.signature, aad + envelope.ciphertext)
    except InvalidSignature as exc:
        raise SyncCryptoError("firma non valida") from exc
    try:
        shared = my_keys.agree_with(envelope.ephemeral)
        return ChaCha20Poly1305(_content_key(shared, envelope.sender, my_id)).decrypt(envelope.nonce, envelope.ciphertext, aad)
    except (InvalidTag, ValueError) as exc:
        raise SyncCryptoError("contenuto manomesso o non decifrabile") from exc
