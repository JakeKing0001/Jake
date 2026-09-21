"""Backup cifrato della memoria, verifica e restore selettivo (F5.7.5).

Formato (versione 1): `JAKEBK01` + lunghezza dell'header (4 byte) + header JSON + testo cifrato.
- chiave: scrypt (n=2^15, r=8, p=1 di default) dalla passphrase e da un sale casuale di 16 byte;
- cifratura: AES-256-GCM con nonce casuale di 12 byte; l'header (parametri, sale, nonce) e' dato come dati
  autenticati aggiuntivi, quindi cambiarne un byte fa fallire la decifratura invece di produrre un
  restore silenziosamente diverso;
- contenuto: JSON compresso con un hash SHA-256 dei dati DENTRO la parte cifrata (verifica di integrita'
  indipendente dal tag GCM).
Nessuna crittografia scritta a mano: `cryptography` (AESGCM, Scrypt) fa tutto; qui c'e' solo il formato.

Una passphrase sbagliata e un file manomesso sono indistinguibili per costruzione (`BackupAuthError`): dire
quale dei due e' avvenuto aiuterebbe solo chi prova a indovinare. Un file che non e' un backup di Jake o e'
troncato e' invece `BackupCorruptError`. Nessun file temporaneo in chiaro: si cifra e si scrive in un colpo.

Il backup contiene anche i ricordi 'secret' e gli embedding (e' pensato per il ripristino completo, non per
la lettura); l'export leggibile (`MemoryPrivacyDashboard.export_*`) li esclude di default."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from core.memory_manager import MemoryManager
from core.memory_privacy import MemoryPrivacyDashboard

MAGIC = b"JAKEBK01"
MIN_PASSPHRASE_LENGTH = 10
DEFAULT_SCRYPT_N = 2**15
_MAX_SCRYPT_N = 2**20  # tetto: un header ostile non deve poter chiedere gigabyte di memoria


class BackupAuthError(Exception):
    """Passphrase errata o file manomesso."""


class BackupCorruptError(Exception):
    """Il file non e' un backup di Jake leggibile (magic sbagliato, troncato, versione sconosciuta)."""


@dataclass(frozen=True)
class BackupInfo:
    records: int
    relations: int
    history_entries: int
    created_at: str
    schema_version: int
    integrity_ok: bool


@dataclass
class RestoreReport:
    restored: int = 0
    skipped_existing: int = 0
    overwritten: int = 0
    filtered_out: int = 0
    relations_restored: int = 0


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


def create_encrypted_backup(memory: MemoryManager, path: Path, passphrase: str, *, include_history: bool = False,
                            scrypt_n: int = DEFAULT_SCRYPT_N) -> BackupInfo:
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise ValueError(f"la passphrase del backup deve avere almeno {MIN_PASSPHRASE_LENGTH} caratteri")
    payload = MemoryPrivacyDashboard(memory).export_payload(include_secret=True, include_embeddings=True)
    history = []
    if include_history:
        with memory.lock:
            history = [dict(r) for r in memory.connection.execute(
                "SELECT role, text, created_at FROM conversation_history ORDER BY id").fetchall()]
    body = json.dumps({"payload": payload, "history": history}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    plaintext = zlib.compress(json.dumps({"sha256": hashlib.sha256(body).hexdigest(), "body": base64.b64encode(body).decode()}).encode())
    salt, nonce = os.urandom(16), os.urandom(12)
    header = json.dumps({
        "v": 1, "kdf": "scrypt", "n": scrypt_n, "r": 8, "p": 1,
        "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
    }, sort_keys=True).encode()
    key = _derive_key(passphrase, salt, scrypt_n, 8, 1)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, header)
    blob = MAGIC + struct.pack(">I", len(header)) + header + ciphertext
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)  # scrittura atomica: mai un backup a meta'
    return BackupInfo(len(payload["records"]), len(payload["relations"]), len(history), payload["exported_at"],
                      payload["schema_version"], True)


def _open_backup(path: Path, passphrase: str) -> tuple[dict, list]:
    blob = Path(path).read_bytes()
    if len(blob) < len(MAGIC) + 4 or not blob.startswith(MAGIC):
        raise BackupCorruptError("non e' un backup cifrato di Jake")
    (header_length,) = struct.unpack(">I", blob[len(MAGIC):len(MAGIC) + 4])
    header_bytes = blob[len(MAGIC) + 4:len(MAGIC) + 4 + header_length]
    if header_length > 4096 or len(header_bytes) != header_length:
        raise BackupCorruptError("header del backup troncato o non valido")
    try:
        header = json.loads(header_bytes)
        if header["v"] != 1 or header["kdf"] != "scrypt":
            raise BackupCorruptError(f"versione del formato non supportata: {header.get('v')!r}")
        n, r, p = int(header["n"]), int(header["r"]), int(header["p"])
        salt, nonce = base64.b64decode(header["salt"]), base64.b64decode(header["nonce"])
    except (ValueError, KeyError, TypeError) as exc:
        raise BackupCorruptError("header del backup illeggibile") from exc
    if not (2**10 <= n <= _MAX_SCRYPT_N and n & (n - 1) == 0) or r != 8 or p != 1:
        raise BackupCorruptError("parametri KDF fuori dai limiti ammessi")
    ciphertext = blob[len(MAGIC) + 4 + header_length:]
    try:
        plaintext = AESGCM(_derive_key(passphrase, salt, n, r, p)).decrypt(nonce, ciphertext, header_bytes)
    except InvalidTag as exc:
        raise BackupAuthError("passphrase errata oppure file manomesso") from exc
    try:
        wrapper = json.loads(zlib.decompress(plaintext))
        body = base64.b64decode(wrapper["body"])
        integrity_ok = hashlib.sha256(body).hexdigest() == wrapper["sha256"]
        content = json.loads(body)
    except (ValueError, KeyError, zlib.error) as exc:
        raise BackupCorruptError("contenuto del backup illeggibile") from exc
    if not integrity_ok:
        raise BackupCorruptError("l'hash del contenuto non corrisponde: backup danneggiato")
    return content["payload"], content.get("history", [])


def verify_backup(path: Path, passphrase: str) -> BackupInfo:
    """Decifra e controlla tutto SENZA scrivere nulla."""
    payload, history = _open_backup(path, passphrase)
    return BackupInfo(len(payload["records"]), len(payload["relations"]), len(history), payload["exported_at"],
                      payload["schema_version"], True)


def restore_from_backup(
    path: Path, passphrase: str, memory: MemoryManager, *, categories: set[str] | None = None,
    keys: set[str] | None = None, on_conflict: str = "skip", include_relations: bool = True,
) -> RestoreReport:
    """Ripristina in `memory`. Selettivo (per categorie e/o chiavi). `on_conflict`: "skip" (default, non
    sovrascrive mai in silenzio), "overwrite" (il backup vince), "newer" (vince l'ultima modifica)."""
    if on_conflict not in ("skip", "overwrite", "newer"):
        raise ValueError("on_conflict deve essere skip, overwrite o newer")
    payload, _ = _open_backup(path, passphrase)
    report = RestoreReport()
    restored_keys: set[tuple[str, str]] = set()
    with memory.lock:
        connection = memory.connection
        for record in payload["records"]:
            if (categories is not None and record["category"] not in categories) or (keys is not None and record["key"] not in keys):
                report.filtered_out += 1
                continue
            existing = connection.execute(
                "SELECT updated_at FROM memories WHERE key = ? AND category = ?", (record["key"], record["category"]),
            ).fetchone()
            if existing is not None:
                if on_conflict == "skip" or (on_conflict == "newer" and existing["updated_at"] >= record["updated_at"]):
                    report.skipped_existing += 1
                    continue
                connection.execute("DELETE FROM memories WHERE key = ? AND category = ?", (record["key"], record["category"]))
                report.overwritten += 1
            columns = [c for c in record if c not in ("id", "has_embedding")]
            connection.execute(
                f"INSERT INTO memories ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [int(record[c]) if c == "pinned" else record[c] for c in columns],
            )
            memory._audit(record["key"], record["category"], "restored", "backup")
            restored_keys.add((record["key"], record["category"]))
            report.restored += 1
        if include_relations:
            for relation in payload["relations"]:
                subject, obj = (relation["subject_key"], relation["subject_category"]), (relation["object_key"], relation["object_category"])
                if subject in restored_keys and obj in restored_keys:
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO memory_relations (subject_key, subject_category, predicate, object_key, "
                        "object_category, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (relation["subject_key"], relation["subject_category"], relation["predicate"],
                         relation["object_key"], relation["object_category"], relation["created_at"]),
                    )
                    report.relations_restored += cursor.rowcount
        connection.commit()
    return report


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
