import os
import subprocess
import sys
from pathlib import Path

from core.skill_result import SkillResult


class OpenPathSkill:
    metadata = {
        "intent": "OPEN_PATH",
        "description": "Apre un file o una cartella con l'applicazione predefinita.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file o della cartella da aprire.",
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

        try:
            if sys.platform == "win32":
                os.startfile(str(target))
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return SkillResult(success=True, data={"path": str(target)})
        except Exception:
            return SkillResult(success=False, data={"path": str(target)}, error="OPERATION_FAILED")
