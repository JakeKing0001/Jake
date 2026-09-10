"""Versioni di prodotto e protocollo lette dal manifest condiviso Python/CMake."""
import json
from pathlib import Path


RELEASE_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "config" / "release.json"


def _load_release_manifest() -> tuple[str, int]:
    manifest = json.loads(RELEASE_MANIFEST_PATH.read_text(encoding="utf-8"))
    version = manifest.get("version")
    protocol_version = manifest.get("protocol_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("config/release.json: version deve essere una stringa non vuota")
    if not isinstance(protocol_version, int) or protocol_version < 1:
        raise ValueError("config/release.json: protocol_version deve essere un intero positivo")
    return version, protocol_version


VERSION, PROTOCOL_VERSION = _load_release_manifest()
