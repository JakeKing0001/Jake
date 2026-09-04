from datetime import datetime
from pathlib import Path

from core.skill_result import SkillResult

NOTES_PATH = Path(__file__).resolve().parent.parent / "data" / "notes.md"


class AddNoteSkill:
    """Appunti cronologici (v3.0): a differenza di REMEMBER/RECALL (chiave-valore, sovrascrive
    per chiave), qui ogni appunto e' una riga in piu' con la sua data, come un vero taccuino."""

    metadata = {
        "intent": "ADD_NOTE",
        "description": "SCRIVE un nuovo appunto nel taccuino di Jake, con data e ora. Usalo solo "
        "quando l'utente sta dettando qualcosa di nuovo da salvare (es. 'appuntati che...', "
        "'scrivi che...', 'segna che...'). Non usarlo per rileggere appunti gia' presi: per "
        "quello c'e' LIST_NOTES.",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Cosa appuntare, con le stesse parole dell'utente.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = f"- [{datetime.now():%Y-%m-%d %H:%M}] {text}\n"
        with open(NOTES_PATH, "a", encoding="utf-8") as handle:
            handle.write(line)

        return SkillResult(success=True, data={"text": text})


class ListNotesSkill:
    metadata = {
        "intent": "LIST_NOTES",
        "description": "LEGGE gli appunti gia' presi in precedenza. Usalo quando l'utente vuole "
        "rivedere/sentire i propri appunti (es. 'leggimi gli appunti', 'che appunti ho preso', "
        "'mostrami il taccuino'), mai per salvarne uno nuovo.",
        "parameters": {
            "limit": {
                "type": "integer",
                "required": False,
                "description": "Quanti appunti recenti mostrare (default 10).",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        limit = parameters.get("limit") or 10

        if not NOTES_PATH.is_file():
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        lines = [line.strip() for line in NOTES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        return SkillResult(success=True, data={"notes": lines[-int(limit):]})
