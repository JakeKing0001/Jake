from pathlib import Path

from core.skill_result import SkillResult


class CreatePathSkill:
    metadata = {
        "intent": "CREATE_PATH",
        "description": "Crea un nuovo file o una nuova cartella.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file o della cartella da creare.",
            },
            "type": {
                "type": "string",
                "required": False,
                "description": (
                    "'file' oppure 'folder'. Se omesso, Jake lo deduce dal percorso "
                    "(un'estensione come .txt indica un file, altrimenti una cartella)."
                ),
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        raw_type = (parameters.get("type") or "").strip().lower()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if raw_type in ("file", "folder"):
            entry_type = raw_type
        else:
            # Nessun 'type' esplicito (capita spesso col planner): un'estensione nel nome
            # suggerisce un file, altrimenti e' piu' sicuro assumere una cartella.
            entry_type = "file" if Path(raw_path).suffix else "folder"

        target = Path(raw_path).expanduser()
        if target.exists():
            return SkillResult(success=False, data={"path": str(target)}, error="ALREADY_EXISTS")

        try:
            if entry_type == "folder":
                target.mkdir(parents=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            return SkillResult(success=True, data={"path": str(target), "type": entry_type})
        except Exception:
            return SkillResult(success=False, data={"path": str(target)}, error="OPERATION_FAILED")
