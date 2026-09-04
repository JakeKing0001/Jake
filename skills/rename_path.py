from pathlib import Path

from core.filesystem_policy import is_protected_path
from core.skill_result import SkillResult


class RenamePathSkill:
    metadata = {
        "intent": "RENAME_PATH",
        "description": "Rinomina un file o una cartella.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file o della cartella da rinominare.",
            },
            "new_name": {
                "type": "string",
                "required": True,
                "description": "Nuovo nome, senza percorso.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        new_name = (parameters.get("new_name") or "").strip()
        if not raw_path or not new_name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        target = Path(raw_path).expanduser()
        if not target.exists():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")
        if is_protected_path(target):
            return SkillResult(success=False, data={"path": str(target)}, error="PROTECTED_PATH")

        destination = target.with_name(new_name)
        if destination.exists():
            return SkillResult(success=False, data={"path": str(destination)}, error="ALREADY_EXISTS")

        try:
            target.rename(destination)
            return SkillResult(success=True, data={"path": str(target), "new_path": str(destination)})
        except Exception:
            return SkillResult(success=False, data={"path": str(target)}, error="OPERATION_FAILED")
