"""Pacchetti di skill firmati, catalogo locale, quarantena e rollback (F8.2): la catena di fornitura di cio' che Jake
importa.

Il principio: **nessun byte di un pacchetto viene interpretato come codice prima di aver superato, nell'ordine,
la firma, l'integrita', la sicurezza dell'archivio e la validazione del manifest** - e anche dopo l'installazione
ogni caricamento ricontrolla i file su disco. Niente di questo modulo esegue codice del pacchetto: non lancia
`setup.py`, non chiama pip, non importa nulla; l'unico import avviene in `plugin_loader.load_skill_package`, dopo
tutto il resto. Il modulo non contiene codice di rete: un pacchetto arriva come byte da un percorso locale, mai da un
URL (F8.2.4).

- `build_package` (F8.2.1): archivio ZIP deterministico - voci in ordine, data fissa, nessun campo extra, solo
  STORED (la compressione dipende dalla versione di zlib, la modalita' STORED no): stessi file = stessi byte =
  stesso digest, ovunque e quando li si costruisca.
- Firma (F8.2.2): Ed25519 staccata, sul digest SHA-256 del pacchetto E su id e versione (un pacchetto non si puo'
  "ribattezzare" come un'altra versione). Le chiavi degli editori stanno in un `TrustStore` che l'utente popola
  esplicitamente; ogni chiave puo' firmare solo id con un certo prefisso (un editore fidato non puo' firmare un id
  che finge di essere di un altro). Firma sconosciuta, chiave revocata, pacchetto alterato, firma non valida,
  archivio con percorsi pericolosi o dimensioni esagerate: tutti rifiutati prima di estrarre alcunche'.
- Catalogo (F8.2.3): per ogni skill le versioni installate con digest, firmatario, permessi (capability e rischio
  massimo), changelog e hash di ogni file. Un aggiornamento che AUMENTA i permessi richiede un'approvazione
  esplicita per quell'aumento.
- Installazione (F8.2.4): richiede un `UserApproval` legato al digest del piano; una versione gia' installata e'
  immutabile (stesso id+versione con contenuto diverso e' un attacco, non un aggiornamento).
- Dipendenze (F8.2.5): solo pacchetti Python GIA' installati, letti dai metadati (`importlib.metadata`, senza
  importarli); mancante o in conflitto = il piano non e' installabile. Jake non esegue pip per una skill.
- Quarantena e revoca (F8.2.6): una versione si mette in quarantena (reversibile) o si revoca (il suo digest non
  si reinstalla piu'); una lista di revoca firmata da un editore fidato si applica in blocco; revocare un
  firmatario mette in quarantena tutto cio' che ha firmato. Un file alterato dopo l'installazione mette in
  quarantena la versione al primo caricamento.
- Pin e rollback (F8.2.7): `pin` blocca la versione attiva (un aggiornamento si installa ma non si attiva);
  `rollback` torna alla versione precedente sana e la blocca.

Limiti dichiarati: la fiducia parte da chi popola il `TrustStore` (nessuna PKI, nessun servizio di
distribuzione); i file di stato sono JSON locali, non protetti da un attaccante che ha gia' accesso in scrittura
alla cartella (il ricontrollo degli hash all'uso rileva le alterazioni dei pacchetti, non del catalogo stesso)."""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from core.risk import RiskLevel, is_at_least
from core.skill_manifest import (
    MANIFEST_FILENAME, Environment, Manifest, ManifestError, parse_manifest, satisfies,
)

PACKAGE_FORMAT_VERSION = 1
SIGNATURE_VERSION = 1
MAX_FILES = 200
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
KEEP_VERSIONS = 3
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_EXCLUDED_DIRS = {"__pycache__", ".git", ".hg", ".svn", ".idea", ".vscode"}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pyd", ".tmp"}
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)", re.IGNORECASE)


class PackageError(Exception):
    """Rifiuto con un `code` stabile (cio' che i test e l'interfaccia guardano) e un messaggio leggibile."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text) -> bytes:
    if not isinstance(text, str):
        raise PackageError("bad_signature", "campo non testuale")
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise PackageError("bad_signature", "base64 non valido") from exc


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---- F8.2.1: formato deterministico -----------------------------------------------------------------------------------------


def _safe_member_name(name: str) -> str | None:
    """Motivo del rifiuto di un nome di voce dell'archivio, o None se e' accettabile."""
    if not name or name != name.strip():
        return "nome vuoto o con spazi ai bordi"
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        return "percorso assoluto o con separatori non portabili"
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "componente vuoto, '.' o '..'"
    if any(_WINDOWS_RESERVED.match(part) or part.endswith((".", " ")) or ":" in part for part in parts):
        return "nome non valido su Windows"
    if any(ord(ch) < 32 for ch in name):
        return "caratteri di controllo"
    return None


def build_package(source_dir: Path) -> bytes:
    """ZIP deterministico dei file di `source_dir`. Rifiuta collegamenti simbolici e file troppo grandi; ignora
    `__pycache__`, `.git`, `.pyc`."""
    source = Path(source_dir).resolve()
    if not (source / MANIFEST_FILENAME).is_file():
        raise PackageError("manifest_missing", f"{MANIFEST_FILENAME} non trovato in {source}")
    files: dict[str, bytes] = {}
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in _EXCLUDED_DIRS for part in relative.parts) or path.suffix in _EXCLUDED_SUFFIXES:
            continue
        if path.is_symlink():
            raise PackageError("unsafe_package", f"{relative}: collegamento simbolico")
        if not path.is_file():
            continue
        name = relative.as_posix()
        problem = _safe_member_name(name)
        if problem:
            raise PackageError("unsafe_package", f"{name}: {problem}")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise PackageError("package_too_large", f"{name}: oltre {MAX_FILE_BYTES} byte")
        files[name] = data
    return pack_files(files)


def pack_files(files: dict[str, bytes]) -> bytes:
    if len(files) > MAX_FILES or sum(len(data) for data in files.values()) > MAX_TOTAL_BYTES:
        raise PackageError("package_too_large", "troppi file o troppi byte")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name in sorted(files, key=lambda item: item.encode("utf-8")):
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.extra = b""
            info.comment = b""
            archive.writestr(info, files[name])
    return buffer.getvalue()


def read_package(data: bytes) -> dict[str, bytes]:
    """Contenuto di un pacchetto, letto IN MEMORIA e dopo ogni controllo di sicurezza sull'archivio. Solo voci
    regolari e STORED, nomi portabili senza duplicati, dimensioni dichiarate coerenti e limitate."""
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise PackageError("unsafe_package", "non e' un archivio valido") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise PackageError("unsafe_package", "numero di file non valido")
        names: set[str] = set()
        total = 0
        for info in infos:
            problem = _safe_member_name(info.filename)
            if problem or info.is_dir():
                raise PackageError("unsafe_package", f"{info.filename!r}: {problem or 'directory'}")
            if info.filename.lower() in names:
                raise PackageError("unsafe_package", f"{info.filename}: duplicato")
            names.add(info.filename.lower())
            mode = info.external_attr >> 16
            if mode and not stat.S_ISREG(mode):
                raise PackageError("unsafe_package", f"{info.filename}: non e' un file regolare")
            if info.compress_type != zipfile.ZIP_STORED or info.compress_size != info.file_size:
                raise PackageError("unsafe_package", f"{info.filename}: solo voci non compresse")
            if info.flag_bits & 0x1:
                raise PackageError("unsafe_package", f"{info.filename}: cifrata")
            if info.file_size > MAX_FILE_BYTES:
                raise PackageError("package_too_large", f"{info.filename}: troppo grande")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise PackageError("package_too_large", "totale oltre il limite")
        files = {}
        for info in infos:
            content = archive.read(info)
            if len(content) != info.file_size:
                raise PackageError("unsafe_package", f"{info.filename}: dimensione incoerente")
            files[info.filename] = content
    return files


# ---- F8.2.2: chiavi, firme, fiducia ------------------------------------------------------------------------------------------


def key_id_of(public_raw: bytes) -> str:
    return sha256_hex(public_raw)[:16]


class PublisherKey:
    """Chiave di firma di un editore (in produzione sta sulla macchina dell'editore, non su quella di chi installa)."""

    def __init__(self, private: Ed25519PrivateKey) -> None:
        self._private = private

    @classmethod
    def generate(cls) -> PublisherKey:
        return cls(Ed25519PrivateKey.generate())

    @property
    def public_raw(self) -> bytes:
        return self._private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    @property
    def key_id(self) -> str:
        return key_id_of(self.public_raw)

    def public_b64(self) -> str:
        return _b64(self.public_raw)

    def sign_package(self, package: bytes) -> dict:
        """Firma staccata per `package`. id e versione vengono letti dal manifest DENTRO il pacchetto."""
        data = _manifest_data(read_package(package))
        skill_id, version = data.get("id"), data.get("version")
        if not (isinstance(skill_id, str) and isinstance(version, str)):
            raise PackageError("manifest_invalid", "id/versione mancanti nel manifest")
        payload = _signed_payload(self.key_id, sha256_hex(package), skill_id, version)
        return {"v": SIGNATURE_VERSION, "key_id": self.key_id, "package_sha256": sha256_hex(package),
                "id": skill_id, "version": version, "signature": _b64(self._private.sign(payload))}

    def sign_revocations(self, revocations: list[dict]) -> dict:
        body = {"v": SIGNATURE_VERSION, "key_id": self.key_id, "revocations": revocations}
        return {**body, "signature": _b64(self._private.sign(_canonical(body)))}


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _signed_payload(key_id: str, digest: str, skill_id: str, version: str) -> bytes:
    return _canonical({"v": SIGNATURE_VERSION, "key_id": key_id, "package_sha256": digest, "id": skill_id, "version": version})


@dataclass
class TrustedKey:
    key_id: str
    name: str
    public_b64: str
    id_prefix: str  # questa chiave puo' firmare solo id che iniziano cosi' (es. "davide.")
    status: str = "active"  # "active" | "revoked"
    revoked_reason: str = ""


class TrustStore:
    """Le chiavi degli editori di cui l'utente si fida, popolate SOLO da un'azione esplicita dell'utente."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._keys: dict[str, TrustedKey] = {}
        self._lock = threading.RLock()
        if self.path is not None and self.path.exists():
            for row in json.loads(self.path.read_text(encoding="utf-8")).get("keys", []):
                self._keys[row["key_id"]] = TrustedKey(**row)

    def _save(self) -> None:
        if self.path is not None:
            _atomic_write(self.path, json.dumps({"v": 1, "keys": [vars(key) for key in self._keys.values()]}, indent=1))

    def add(self, name: str, public_b64: str, id_prefix: str) -> TrustedKey:
        raw = _unb64(public_b64)
        if len(raw) != 32:
            raise PackageError("bad_key", "una chiave Ed25519 pubblica e' di 32 byte")
        if not re.fullmatch(r"[a-z][a-z0-9_]*\.", id_prefix):
            raise PackageError("bad_key", "il prefisso deve essere del tipo 'davide.'")
        key = TrustedKey(key_id_of(raw), name, public_b64, id_prefix)
        with self._lock:
            existing = self._keys.get(key.key_id)
            if existing is not None and existing.status == "revoked":
                raise PackageError("signer_revoked", "una chiave revocata non si reintroduce")
            self._keys[key.key_id] = key
            self._save()
        return key

    def get(self, key_id: str) -> TrustedKey | None:
        return self._keys.get(key_id)

    def revoke(self, key_id: str, reason: str) -> bool:
        with self._lock:
            key = self._keys.get(key_id)
            if key is None or key.status == "revoked":
                return False
            key.status, key.revoked_reason = "revoked", reason
            self._save()
            return True

    def keys(self) -> list[TrustedKey]:
        return list(self._keys.values())


@dataclass(frozen=True)
class VerifiedPackage:
    digest: str
    key_id: str
    manifest: Manifest
    files: dict[str, bytes] = field(repr=False)


def _manifest_data(files: dict[str, bytes]) -> dict:
    raw = files.get(MANIFEST_FILENAME)
    if raw is None:
        raise PackageError("manifest_missing", f"{MANIFEST_FILENAME} assente nel pacchetto")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PackageError("manifest_invalid", "JSON non valido") from exc
    if not isinstance(data, dict):
        raise PackageError("manifest_invalid", "il manifest deve essere un oggetto")
    return data


def _manifest_of(files: dict[str, bytes], env: Environment, existing_intents=()) -> Manifest:
    try:
        manifest = parse_manifest(_manifest_data(files), env, existing_intents)
    except ManifestError as exc:
        raise PackageError("manifest_invalid", "; ".join(exc.errors)) from exc
    missing = [name for name in [manifest.entry, *manifest.tests["files"]] if name not in files]
    if missing:
        raise PackageError("manifest_invalid", f"file dichiarati ma assenti nel pacchetto: {missing}")
    return manifest


def verify_package(package: bytes, signature: dict, trust: TrustStore, env: Environment | None = None,
                   existing_intents=()) -> VerifiedPackage:
    """Tutti i controlli che precedono l'installazione, nell'ordine in cui costano meno e fidano meno: struttura della
    firma, firmatario noto e attivo, digest del pacchetto, firma crittografica, archivio sicuro, manifest valido e
    coerente con quanto firmato, prefisso dell'id consentito al firmatario."""
    if not isinstance(signature, dict) or signature.get("v") != SIGNATURE_VERSION:
        raise PackageError("bad_signature", "formato della firma non supportato")
    key_id, digest = signature.get("key_id"), signature.get("package_sha256")
    skill_id, version = signature.get("id"), signature.get("version")
    if not all(isinstance(item, str) for item in (key_id, digest, skill_id, version)):
        raise PackageError("bad_signature", "campi mancanti")
    trusted = trust.get(key_id)  # type: ignore[arg-type]
    if trusted is None:
        raise PackageError("unknown_signer", f"chiave {key_id} non presente tra quelle fidate")
    if trusted.status != "active":
        raise PackageError("signer_revoked", f"chiave {key_id} revocata: {trusted.revoked_reason or 'senza motivo'}")
    actual = sha256_hex(package)
    if actual != digest:
        raise PackageError("package_altered", "il digest del pacchetto non corrisponde a quello firmato")
    public = Ed25519PublicKey.from_public_bytes(_unb64(trusted.public_b64))
    try:
        public.verify(_unb64(signature.get("signature")), _signed_payload(key_id, digest, skill_id, version))  # type: ignore[arg-type]
    except InvalidSignature as exc:
        raise PackageError("bad_signature", "la firma non e' valida") from exc
    files = read_package(package)
    manifest = _manifest_of(files, env or Environment(), existing_intents)
    if (manifest.id, manifest.version) != (skill_id, version):
        raise PackageError("manifest_mismatch", "id/versione del manifest diversi da quelli firmati")
    if not manifest.id.startswith(trusted.id_prefix):
        raise PackageError("id_not_allowed", f"la chiave {key_id} puo' firmare solo id '{trusted.id_prefix}*'")
    return VerifiedPackage(actual, key_id, manifest, files)  # type: ignore[arg-type]


# ---- F8.2.4: piano e approvazione ---------------------------------------------------------------------------------------------


def installed_python_version(name: str) -> str | None:
    """Versione di un pacchetto Python installato, dai METADATI (senza importarlo). Solo le prime tre componenti numeriche."""
    try:
        raw = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", raw)
    return ".".join(part or "0" for part in match.groups()) if match else None


@dataclass
class InstallPlan:
    verified: VerifiedPackage
    previous_version: str | None
    is_downgrade: bool
    added_capabilities: list[str]
    risk_increased: bool
    blockers: list[str]
    summary: str

    @property
    def permissions_increased(self) -> bool:
        return bool(self.added_capabilities) or self.risk_increased


@dataclass(frozen=True)
class UserApproval:
    digest: str
    actor: str
    allow_permission_increase: bool = False
    allow_downgrade: bool = False


def approve(plan: InstallPlan, actor: str, *, allow_permission_increase: bool = False,
            allow_downgrade: bool = False) -> UserApproval:
    """Da chiamare SOLO da un'azione esplicita dell'utente (HUD/CLI dopo aver mostrato `plan.summary`)."""
    return UserApproval(plan.verified.digest, actor, allow_permission_increase, allow_downgrade)


# ---- catalogo ---------------------------------------------------------------------------------------------------------------------


class SkillStore:
    """Cartella delle skill installate: `<root>/store/<id>/<versione>/`, `<root>/catalog.json`."""

    def __init__(self, root: Path, trust: TrustStore, env: Environment | None = None,
                 installed_versions: Callable[[str], str | None] = installed_python_version,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.trust = trust
        self.env = env or Environment()
        self._installed_versions = installed_versions
        self._clock = clock
        self._lock = threading.RLock()
        self._catalog_path = self.root / "catalog.json"
        self._data: dict = {"v": 1, "skills": {}, "denied_digests": {}}
        if self._catalog_path.exists():
            self._data = json.loads(self._catalog_path.read_text(encoding="utf-8"))

    # ---- persistenza ----
    def _save(self) -> None:
        _atomic_write(self._catalog_path, json.dumps(self._data, indent=1, sort_keys=True))

    def _entry(self, skill_id: str) -> dict:
        try:
            return self._data["skills"][skill_id]
        except KeyError:
            raise PackageError("not_installed", skill_id) from None

    def _version_dir(self, skill_id: str, version: str) -> Path:
        return self.root / "store" / skill_id / version

    # ---- lettura ----
    def skills(self) -> list[str]:
        return sorted(self._data["skills"])

    def describe(self, skill_id: str) -> dict:
        """Catalogo di una skill: versioni con permessi, changelog e stato (F8.2.3)."""
        entry = self._entry(skill_id)
        return {
            "id": skill_id, "active": entry["active"], "pinned": entry["pinned"],
            "versions": {version: {key: info[key] for key in ("status", "status_reason", "digest", "key_id", "installed_at",
                                                              "capabilities", "highest_risk", "changelog")}
                         for version, info in sorted(entry["versions"].items())},
        }

    def active_version(self, skill_id: str) -> str | None:
        return self._entry(skill_id)["active"]

    # ---- F8.2.4/F8.2.5: piano ----
    def plan_install(self, package: bytes, signature: dict) -> InstallPlan:
        with self._lock:
            existing = {intent for entry in self._data["skills"].values() for info in entry["versions"].values() for intent in info["intents"]}
            verified = verify_package(package, signature, self.trust, self.env)
            manifest = verified.manifest
            entry = self._data["skills"].get(manifest.id)
            # Gli intent di ALTRE skill installate non possono essere presi; quelli della stessa skill sono ovvi per un aggiornamento.
            own = set()
            if entry:
                own = {intent for info in entry["versions"].values() for intent in info["intents"]}
            clash = sorted((existing - own) & manifest.intent_names())
            blockers: list[str] = []
            if clash:
                blockers.append(f"intent_collision: {clash} appartengono a un'altra skill installata")
            if verified.digest in self._data["denied_digests"]:
                blockers.append(f"digest_revoked: {self._data['denied_digests'][verified.digest]}")
            previous_version = None
            added: list[str] = []
            risk_increased = False
            downgrade = False
            if entry:
                same = entry["versions"].get(manifest.version)
                if same is not None and same["digest"] != verified.digest:
                    blockers.append("version_conflict: la versione esiste gia' con contenuto diverso (una versione e' immutabile)")
                if same is not None and same["digest"] == verified.digest:
                    blockers.append("already_installed: stessa versione e stesso contenuto")
                previous_version = entry["active"]
                if previous_version is not None:
                    prev = entry["versions"][previous_version]
                    added = sorted(set(manifest.capabilities()) - set(prev["capabilities"]))
                    previous_risk = RiskLevel(prev["highest_risk"])
                    risk_increased = manifest.highest_risk() != previous_risk and is_at_least(manifest.highest_risk(), previous_risk)
                    downgrade = _version_tuple(manifest.version) < _version_tuple(previous_version)
            blockers.extend(self._dependency_blockers(manifest))
            return InstallPlan(verified, previous_version, downgrade, added, risk_increased, blockers, _plan_summary(verified, added, risk_increased, downgrade, blockers))

    def _dependency_blockers(self, manifest: Manifest) -> list[str]:
        blockers = []
        for dep in manifest.dependencies:
            found = self._installed_versions(dep["name"])
            if found is None:
                blockers.append(f"dependency_missing: {dep['name']}{dep['spec']} non e' installato (Jake non esegue pip per una skill)")
            elif not satisfies(found, dep["spec"]):
                blockers.append(f"dependency_conflict: {dep['name']}{dep['spec']} richiesto, installato {found}")
        return blockers

    # ---- installazione ----
    def install(self, plan: InstallPlan, approval: UserApproval | None) -> dict:
        with self._lock:
            if approval is None or not isinstance(approval, UserApproval) or not approval.actor.strip():
                raise PackageError("approval_required", "serve l'approvazione esplicita dell'utente")
            if approval.digest != plan.verified.digest:
                raise PackageError("approval_mismatch", "l'approvazione riguarda un altro pacchetto")
            if plan.blockers:
                raise PackageError("blocked", "; ".join(plan.blockers))
            if plan.permissions_increased and not approval.allow_permission_increase:
                raise PackageError("permissions_increased", f"nuove capability {plan.added_capabilities} o rischio piu' alto: serve l'approvazione dell'aumento")
            if plan.is_downgrade and not approval.allow_downgrade:
                raise PackageError("downgrade_not_approved", "versione piu' vecchia di quella attiva")
            verified, manifest = plan.verified, plan.verified.manifest
            final = self._version_dir(manifest.id, manifest.version)
            staging = self.root / ".staging" / uuid.uuid4().hex
            try:
                for name, content in verified.files.items():
                    target = (staging / name).resolve()
                    target.relative_to(staging.resolve())  # difesa in profondita': read_package ha gia' controllato i nomi
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                final.parent.mkdir(parents=True, exist_ok=True)
                if final.exists():
                    raise PackageError("version_conflict", str(final))
                os.replace(staging, final)
            finally:
                shutil.rmtree(self.root / ".staging" / staging.name, ignore_errors=True)
            entry = self._data["skills"].setdefault(manifest.id, {"active": None, "pinned": None, "history": [], "versions": {}})
            notes = next((item["notes"] for item in manifest.changelog if item["version"] == manifest.version), "")
            entry["versions"][manifest.version] = {
                "digest": verified.digest, "key_id": verified.key_id, "installed_at": self._clock(), "status": "ok",
                "status_reason": "", "capabilities": sorted(manifest.capabilities()), "highest_risk": manifest.highest_risk().value,
                "changelog": notes, "intents": sorted(manifest.intent_names()),
                "files": {name: sha256_hex(content) for name, content in verified.files.items()},
            }
            activated = entry["pinned"] in (None, manifest.version)
            if activated:
                entry["active"] = manifest.version
                entry["history"].append(manifest.version)
            self._prune(manifest.id)
            self._save()
            return {"id": manifest.id, "version": manifest.version, "activated": activated,
                    "reason": "" if activated else f"bloccata sulla versione {entry['pinned']} (pin)"}

    def _prune(self, skill_id: str) -> None:
        entry = self._data["skills"][skill_id]
        protected = {entry["active"], entry["pinned"], *entry["history"][-KEEP_VERSIONS:]}
        removable = sorted((v for v in entry["versions"] if v not in protected), key=_version_tuple)
        while len(entry["versions"]) > KEEP_VERSIONS and removable:
            version = removable.pop(0)
            shutil.rmtree(self._version_dir(skill_id, version), ignore_errors=True)
            del entry["versions"][version]

    # ---- integrita' e uso ----
    def verify_installed(self, skill_id: str, version: str) -> list[str]:
        """Problemi dei file su disco rispetto agli hash registrati all'installazione (lista vuota = integro)."""
        info = self._entry(skill_id)["versions"].get(version)
        if info is None:
            return ["versione non installata"]
        base = self._version_dir(skill_id, version)
        problems = []
        for name, digest in info["files"].items():
            path = base / name
            if not path.is_file():
                problems.append(f"{name}: mancante")
            elif sha256_hex(path.read_bytes()) != digest:
                problems.append(f"{name}: alterato")
        on_disk = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()} if base.exists() else set()
        problems.extend(f"{name}: file estraneo non presente nel pacchetto firmato" for name in sorted(on_disk - set(info["files"])))
        return problems

    def usable_directory(self, skill_id: str) -> Path:
        """Cartella della versione attiva, SOLO se sana: stato `ok` e file integri. Una versione alterata viene messa in
        quarantena e si ripiega sulla precedente sana; se non ce n'e', errore."""
        with self._lock:
            for _ in range(len(self._entry(skill_id)["versions"]) + 1):
                entry = self._entry(skill_id)
                version = entry["active"]
                if version is None:
                    raise PackageError("no_usable_version", f"{skill_id}: nessuna versione utilizzabile")
                info = entry["versions"][version]
                if info["status"] != "ok":
                    self._fallback(skill_id)
                    continue
                # Il bytecode in cache non e' contenuto firmato: un .pyc preparato da un attaccante potrebbe sostituire il
                # sorgente verificato. Si cancella sempre prima di controllare, e il caricamento non ne scrive di nuovi.
                for cache in self._version_dir(skill_id, version).rglob("__pycache__"):
                    shutil.rmtree(cache, ignore_errors=True)
                problems = self.verify_installed(skill_id, version)
                if problems:
                    self.quarantine(skill_id, version, "tampered: " + "; ".join(problems[:3]))
                    continue
                return self._version_dir(skill_id, version)
            raise PackageError("no_usable_version", f"{skill_id}: nessuna versione utilizzabile")

    def load(self, registry, skill_id: str, logger=None):
        """Carica la skill attiva: integrita' ricontrollata sul disco, POI il caricamento con validazione del manifest."""
        from core.plugin_loader import load_skill_package  # import locale: plugin_loader importa skill_manifest, non questo modulo
        import sys
        directory = self.usable_directory(skill_id)
        previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            return load_skill_package(registry, directory, self.env, logger)
        finally:
            sys.dont_write_bytecode = previous

    # ---- F8.2.6: quarantena e revoca ----
    def _fallback(self, skill_id: str) -> None:
        """Riporta la versione attiva all'ultima sana della cronologia (o a nessuna)."""
        entry = self._entry(skill_id)
        for index in range(len(entry["history"]) - 1, -1, -1):
            version = entry["history"][index]
            if version != entry["active"] and entry["versions"].get(version, {}).get("status") == "ok":
                entry["active"] = version
                entry["history"] = entry["history"][: index + 1]
                return
        entry["active"] = None

    def quarantine(self, skill_id: str, version: str, reason: str) -> None:
        with self._lock:
            entry = self._entry(skill_id)
            info = entry["versions"].get(version)
            if info is None:
                raise PackageError("not_installed", f"{skill_id} {version}")
            if info["status"] == "ok":
                info["status"], info["status_reason"] = "quarantined", reason
            if entry["active"] == version:
                self._fallback(skill_id)
            self._save()

    def release(self, skill_id: str, version: str) -> None:
        """Toglie una versione dalla quarantena SE i suoi file sono integri (una revocata non si rilascia)."""
        with self._lock:
            info = self._entry(skill_id)["versions"].get(version)
            if info is None:
                raise PackageError("not_installed", f"{skill_id} {version}")
            if info["status"] == "revoked":
                raise PackageError("revoked", "una versione revocata non si rilascia")
            problems = self.verify_installed(skill_id, version)
            if problems:
                raise PackageError("tampered", "; ".join(problems[:3]))
            info["status"], info["status_reason"] = "ok", ""
            self._save()

    def revoke_version(self, skill_id: str, version: str, reason: str) -> None:
        with self._lock:
            entry = self._entry(skill_id)
            info = entry["versions"].get(version)
            if info is None:
                raise PackageError("not_installed", f"{skill_id} {version}")
            info["status"], info["status_reason"] = "revoked", reason
            self._data["denied_digests"][info["digest"]] = reason
            if entry["active"] == version:
                self._fallback(skill_id)
            self._save()

    def apply_revocation_list(self, signed: dict) -> int:
        """Applica una lista di revoca firmata da un editore fidato: ogni voce `{id, version, digest, reason}` revoca la
        versione e nega il digest anche se non e' installata. Ritorna quante voci hanno avuto effetto."""
        if not isinstance(signed, dict) or signed.get("v") != SIGNATURE_VERSION or not isinstance(signed.get("revocations"), list):
            raise PackageError("bad_signature", "lista di revoca malformata")
        trusted = self.trust.get(str(signed.get("key_id")))
        if trusted is None or trusted.status != "active":
            raise PackageError("unknown_signer", "lista di revoca di un firmatario non fidato")
        body = {key: signed[key] for key in ("v", "key_id", "revocations")}
        try:
            Ed25519PublicKey.from_public_bytes(_unb64(trusted.public_b64)).verify(_unb64(signed.get("signature")), _canonical(body))
        except InvalidSignature as exc:
            raise PackageError("bad_signature", "firma della lista di revoca non valida") from exc
        applied = 0
        with self._lock:
            for item in signed["revocations"]:
                if not (isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("digest"), str)):
                    continue
                # Un editore puo' revocare solo cio' che potrebbe firmare.
                if not item["id"].startswith(trusted.id_prefix):
                    continue
                reason = str(item.get("reason", "revocata dall'editore"))
                self._data["denied_digests"][item["digest"]] = reason
                entry = self._data["skills"].get(item["id"])
                if entry:
                    for version, info in entry["versions"].items():
                        if info["digest"] == item["digest"] and info["status"] != "revoked":
                            info["status"], info["status_reason"] = "revoked", reason
                            if entry["active"] == version:
                                self._fallback(item["id"])
                applied += 1
            self._save()
        return applied

    def revoke_signer(self, key_id: str, reason: str) -> list[tuple[str, str]]:
        """Revoca la chiave e mette in QUARANTENA (non revoca: serve una revisione) tutto cio' che ha firmato."""
        with self._lock:
            self.trust.revoke(key_id, reason)
            affected = []
            for skill_id, entry in self._data["skills"].items():
                for version, info in list(entry["versions"].items()):
                    if info["key_id"] == key_id and info["status"] == "ok":
                        info["status"], info["status_reason"] = "quarantined", f"firmatario revocato: {reason}"
                        affected.append((skill_id, version))
                if entry["active"] and entry["versions"][entry["active"]]["status"] != "ok":
                    self._fallback(skill_id)
            self._save()
            return affected

    # ---- F8.2.7: pin e rollback ----
    def pin(self, skill_id: str, version: str) -> None:
        with self._lock:
            entry = self._entry(skill_id)
            info = entry["versions"].get(version)
            if info is None or info["status"] != "ok":
                raise PackageError("not_usable", f"{skill_id} {version} non e' installata o non e' sana")
            entry["pinned"] = version
            self._activate(skill_id, version)
            self._save()

    def unpin(self, skill_id: str) -> None:
        with self._lock:
            self._entry(skill_id)["pinned"] = None
            self._save()

    def _activate(self, skill_id: str, version: str) -> None:
        entry = self._entry(skill_id)
        if entry["active"] != version:
            entry["active"] = version
            entry["history"].append(version)

    def rollback(self, skill_id: str) -> str:
        """Torna alla versione precedente SANA (stato ok, file integri) e la blocca con un pin: senza il pin la prossima
        installazione riattiverebbe proprio la versione da cui si e' scappati."""
        with self._lock:
            entry = self._entry(skill_id)
            current = entry["active"]
            for version in reversed(entry["history"]):
                if version == current:
                    continue
                info = entry["versions"].get(version)
                if info and info["status"] == "ok" and not self.verify_installed(skill_id, version):
                    entry["active"] = version
                    entry["history"].append(version)
                    entry["pinned"] = version
                    self._save()
                    return version
            raise PackageError("no_previous_version", f"{skill_id}: nessuna versione precedente sana")


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _plan_summary(verified: VerifiedPackage, added: list[str], risk_increased: bool, downgrade: bool, blockers: list[str]) -> str:
    from core.skill_manifest import permission_summary
    lines = [permission_summary(verified.manifest), f"Firmato da: {verified.key_id}  Digest: {verified.digest[:16]}..."]
    if added:
        lines.append(f"ATTENZIONE - nuove capability rispetto alla versione attiva: {', '.join(added)}")
    if risk_increased:
        lines.append("ATTENZIONE - il rischio massimo e' aumentato rispetto alla versione attiva")
    if downgrade:
        lines.append("ATTENZIONE - e' una versione piu' vecchia di quella attiva")
    lines.extend(f"BLOCCO - {item}" for item in blockers)
    return "\n".join(lines)
