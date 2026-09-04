import shutil
from pathlib import Path

from core.filesystem_policy import is_protected_path
from core.skill_result import SkillResult


class DeletePathSkill:
    metadata = {
        "intent": "DELETE_PATH",
        "description": "Elimina un file o una cartella. Richiede sempre conferma.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file o della cartella da eliminare.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        target = Path(raw_path).expanduser()
        if not target.exists():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")
        if is_protected_path(target):
            return SkillResult(success=False, data={"path": str(target)}, error="PROTECTED_PATH")

        if not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "path": str(target),
                    "message": f"Sei sicuro di voler eliminare {target}?",
                    "confirm_parameters": {"path": str(target), "confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return SkillResult(success=True, data={"path": str(target)})
        except Exception:
            return SkillResult(success=False, data={"path": str(target)}, error="OPERATION_FAILED")
