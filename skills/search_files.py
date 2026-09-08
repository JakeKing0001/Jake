from core.nest_search import run_nest_search
from core.skill_result import SkillResult


class SearchFilesSkill:
    """Ricerca per nome/contenuto nell'indice locale di NEST (skill/tool di Jake)."""

    metadata = {
        "intent": "SEARCH_FILES",
        "description": "Cerca file per nome o contenuto nell'indice locale di NEST.",
        "parameters": {
            "query": {
                "type": "string",
                "required": True,
                "description": "Testo da cercare tra i file indicizzati.",
            },
        },
    }

    def __init__(self, nest_client, conversation_state):
        self.nest_client = nest_client
        self.conversation_state = conversation_state

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        query = (parameters.get("query") or "").strip()
        if not query:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        return run_nest_search(self.nest_client, self.conversation_state, query, self.nest_client.search)
