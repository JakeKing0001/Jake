import subprocess
from pathlib import Path

from core.skill_result import SkillResult


class OpenInEditorSkill:
    """Apre una cartella o un file in Visual Studio Code (richiede 'code' nel PATH, installato
    di default dal setup di VS Code con l'opzione "Aggiungi a PATH")."""

    metadata = {
        "intent": "OPEN_IN_EDITOR",
        "description": "Apre un progetto/cartella/file in Visual Studio Code. Usalo per richieste "
        "come 'apri il progetto X in vscode', diverso da OPEN_APP che apre solo l'editor vuoto.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso della cartella o del file da aprire in Visual Studio Code.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.exists():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        try:
            subprocess.Popen(["code", str(path)], shell=True)
        except Exception:
            return SkillResult(success=False, data={"path": raw_path}, error="LAUNCH_FAILED")

        return SkillResult(success=True, data={"path": str(path)})
