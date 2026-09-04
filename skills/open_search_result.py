import os
import subprocess
import sys
from pathlib import Path

from core.skill_result import SkillResult


class OpenSearchResultSkill:
    """Apre un file trovato dall'ultima ricerca NEST, per posizione o percorso completo."""

    metadata = {
        "intent": "OPEN_SEARCH_RESULT",
        "description": "Apre un risultato dell'ultima ricerca file effettuata (per posizione o percorso).",
        "parameters": {
            "index": {
                "type": "integer",
                "required": False,
                "description": "Posizione del risultato nell'ultima ricerca (1 = primo).",
            },
            "path": {
                "type": "string",
                "required": False,
                "description": "Percorso completo del file da aprire, se noto.",
            },
        },
    }

    def __init__(self, conversation_state):
        self.conversation_state = conversation_state

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        path = (parameters.get("path") or "").strip()
        index = parameters.get("index")

        if not path and index:
            results = self.conversation_state.get_last_search_results()
            position = int(index) - 1
            if 0 <= position < len(results):
                path = results[position]["path"]

        if not path:
            return SkillResult(success=False, data={}, error="RESULT_NOT_FOUND")

        target = Path(path)
        if not target.exists():
            return SkillResult(success=False, data={"path": path}, error="RESULT_NOT_FOUND")

        try:
            if sys.platform == "win32":
                os.startfile(str(target))
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return SkillResult(success=True, data={"path": str(target)})
        except Exception:
            return SkillResult(success=False, data={"path": str(target)}, error="OPERATION_FAILED")
