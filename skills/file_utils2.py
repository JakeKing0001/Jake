import hashlib
from pathlib import Path

from core.skill_result import SkillResult


class FindLargeFilesSkill:
    metadata = {
        "intent": "FIND_LARGE_FILES",
        "description": "Trova i file piu' grandi di una certa dimensione dentro una cartella, per aiutare a liberare spazio.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Cartella in cui cercare."},
            "min_size_mb": {"type": "number", "required": False, "description": "Dimensione minima in MB (default 100)."},
        },
    }

    MAX_RESULTS = 15
    MAX_SCANNED = 20000

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        min_size_mb = parameters.get("min_size_mb") if isinstance(parameters.get("min_size_mb"), (int, float)) else 100
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_dir():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        min_size_bytes = min_size_mb * 1024 * 1024
        results = []
        scanned = 0
        for entry in path.rglob("*"):
            scanned += 1
            if scanned > self.MAX_SCANNED:
                break
            try:
                if entry.is_file() and entry.stat().st_size >= min_size_bytes:
                    results.append({"path": str(entry), "size_mb": round(entry.stat().st_size / (1024 ** 2), 1)})
            except OSError:
                continue

        results.sort(key=lambda item: item["size_mb"], reverse=True)
        if not results:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"files": results[:self.MAX_RESULTS]})


class FindDuplicateFilesSkill:
    """Confronta i file per hash del contenuto (non solo nome/dimensione): due file identici
    ma rinominati vengono comunque riconosciuti come duplicati."""

    metadata = {
        "intent": "FIND_DUPLICATE_FILES",
        "description": "Trova file duplicati (stesso contenuto) dentro una cartella.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Cartella in cui cercare duplicati."},
        },
    }

    MAX_SCANNED = 5000
    MAX_GROUPS = 10

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_dir():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        hashes: dict[str, list[str]] = {}
        scanned = 0
        for entry in path.rglob("*"):
            if not entry.is_file():
                continue
            scanned += 1
            if scanned > self.MAX_SCANNED:
                break
            try:
                digest = hashlib.md5(entry.read_bytes()).hexdigest()
            except OSError:
                continue
            hashes.setdefault(digest, []).append(str(entry))

        duplicate_groups = [group for group in hashes.values() if len(group) > 1][:self.MAX_GROUPS]
        if not duplicate_groups:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"duplicate_groups": duplicate_groups})
