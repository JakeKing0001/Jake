import shutil
from pathlib import Path

from core.filesystem_policy import is_protected_path
from core.skill_result import SkillResult


class MovePathSkill:
    metadata = {
        "intent": "MOVE_PATH",
        "description": "Sposta un file o una cartella in una nuova posizione. Richiede sempre conferma.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file o della cartella da spostare.",
            },
            "destination": {
                "type": "string",
                "required": True,
                "description": "Cartella di destinazione.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        raw_destination = (parameters.get("destination") or "").strip()
        if not raw_path or not raw_destination:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        source = Path(raw_path).expanduser()
        destination_dir = Path(raw_destination).expanduser()
        if not source.exists():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")
        if is_protected_path(source):
            return SkillResult(success=False, data={"path": str(source)}, error="PROTECTED_PATH")

        destination = destination_dir / source.name
        if destination.exists():
            return SkillResult(success=False, data={"path": str(destination)}, error="ALREADY_EXISTS")

        if not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "path": str(source),
                    "destination": str(destination_dir),
                    "message": f"Confermi di voler spostare {source} in {destination_dir}?",
                    "confirm_parameters": {
                        "path": str(source),
                        "destination": str(destination_dir),
                        "confirmed": True,
                    },
                },
                error="CONFIRMATION_REQUIRED",
            )

        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            return SkillResult(success=True, data={"path": str(source), "new_path": str(destination)})
        except Exception:
            return SkillResult(success=False, data={"path": str(source)}, error="OPERATION_FAILED")
