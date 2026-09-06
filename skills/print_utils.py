import os
from pathlib import Path

from core.skill_result import SkillResult


class PrintFileSkill:
    """Invia un file alla stampante predefinita usando il verbo 'print' di Windows (ShellExecute
    tramite os.startfile): funziona per qualunque tipo di file che abbia un'applicazione
    associata capace di stamparlo (PDF, documenti, immagini), senza dover gestire i driver."""

    metadata = {
        "intent": "PRINT_FILE",
        "description": "Stampa un file sulla stampante predefinita.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Percorso del file da stampare."},
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

        try:
            os.startfile(str(path), "print")
        except OSError:
            return SkillResult(success=False, data={"path": raw_path}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"path": raw_path})
