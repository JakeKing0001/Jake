from pathlib import Path

from core.skill_result import SkillResult


class FindFileSkill:
    """Ricerca ricorsiva locale per nome file, senza indice (per la ricerca via NEST vedi SEARCH_FILES)."""

    metadata = {
        "intent": "FIND_FILE",
        "description": "Cerca file per nome in una cartella, scandendola ricorsivamente.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome, o parte del nome, del file da cercare.",
            },
            "path": {
                "type": "string",
                "required": False,
                "description": "Cartella in cui cercare. Default: cartella utente.",
            },
        },
    }

    MAX_RESULTS = 15

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip().lower()
        raw_root = (parameters.get("path") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        root = Path(raw_root).expanduser() if raw_root else Path.home()
        if not root.is_dir():
            return SkillResult(success=False, data={"path": str(root)}, error="PATH_NOT_FOUND")

        matches = []
        try:
            for entry in root.rglob("*"):
                if name in entry.name.lower():
                    matches.append(str(entry))
                    if len(matches) >= self.MAX_RESULTS:
                        break
        except (PermissionError, OSError):
            pass

        if not matches:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")
        return SkillResult(success=True, data={"name": name, "results": matches})
