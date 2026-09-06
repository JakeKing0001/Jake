import hashlib
import re
from pathlib import Path

from core.skill_result import SkillResult


class CheckPasswordStrengthSkill:
    metadata = {
        "intent": "CHECK_PASSWORD_STRENGTH",
        "description": "Valuta quanto e' sicura una password.",
        "parameters": {
            "password": {"type": "string", "required": True, "description": "La password da valutare."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        password = parameters.get("password") or ""
        if not password:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        score = 0
        if len(password) >= 8:
            score += 1
        if len(password) >= 12:
            score += 1
        if re.search(r"[a-z]", password) and re.search(r"[A-Z]", password):
            score += 1
        if re.search(r"\d", password):
            score += 1
        if re.search(r"[^a-zA-Z0-9]", password):
            score += 1

        labels = {0: "molto debole", 1: "molto debole", 2: "debole", 3: "discreta", 4: "forte", 5: "molto forte"}
        return SkillResult(success=True, data={"strength": labels[score], "score": score})


class CheckFileHashSkill:
    metadata = {
        "intent": "CHECK_FILE_HASH",
        "description": "Calcola l'impronta SHA-256 di un file, utile per verificare l'integrita' di un download.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Percorso del file di cui calcolare l'hash."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_file():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
        except OSError:
            return SkillResult(success=False, data={"path": raw_path}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"path": raw_path, "sha256": digest.hexdigest()})
