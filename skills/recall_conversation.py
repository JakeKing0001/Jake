"""F5.2 (memoria episodica) nel runtime: la cronologia delle conversazioni era salvata (MemoryManager.log_turn) ma
nessun comando la interrogava. "Di cosa abbiamo parlato ieri?", "cosa ti ho chiesto sulla palestra la settimana
scorsa?" - le richieste dell'utente in quel periodo, con l'ora, dalla cronologia vera (del profilo attivo: F2.7
cambia database per profilo). Sola lettura; in modalita' privata non c'e' nulla perche' nulla viene registrato."""
from datetime import datetime

from core.skill_result import SkillResult
from core.temporal_parser import find_relative_range

MAX_ITEMS = 8


class RecallConversationSkill:
    metadata = {
        "intent": "RECALL_CONVERSATION",
        "description": (
            "Ricorda di cosa si e' parlato con Jake in un periodo (le richieste dell'utente, con l'ora). Per 'di cosa "
            "abbiamo parlato ieri?', 'cosa ti ho chiesto stamattina?', 'cosa ti ho detto sulla palestra la settimana "
            "scorsa?'. Non per i fatti salvati (quelli sono RECALL)."
        ),
        "parameters": {
            "when": {"type": "string", "required": False,
                     "description": "Il periodo con le parole dell'utente: 'ieri', 'oggi', 'la settimana scorsa'..."},
            "topic": {"type": "string", "required": False, "description": "Argomento, se l'utente lo dice."},
        },
    }

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        when = str(parameters.get("when") or "oggi").strip()
        topic = str(parameters.get("topic") or "").strip()
        found = find_relative_range(when)
        if found is None:
            return SkillResult(success=False, data={"when": when}, error="INVALID_TIME_RANGE")
        (since, until), phrase = found
        turns = self.memory_manager.history_between(since, until, topic=topic or None, role="user")
        items = [{"at": _local_time(t["created_at"]), "text": t["text"]} for t in turns[-MAX_ITEMS:]]
        return SkillResult(success=True, data={"when": phrase, "topic": topic, "items": items,
                                               "omitted": max(0, len(turns) - MAX_ITEMS)})


def _local_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    except ValueError:
        return ""


def format_recall_conversation(data: dict) -> str:
    about = f" su {data['topic']}" if data.get("topic") else ""
    if not data.get("items"):
        return f"Non trovo conversazioni nostre{about} per \"{data['when']}\"."
    lines = "; ".join(f"alle {i['at']} \"{i['text']}\"" for i in data["items"])
    more = f" (e altre {data['omitted']} prima)" if data.get("omitted") else ""
    return f"{data['when'].capitalize()}{about} mi hai chiesto: {lines}{more}."
