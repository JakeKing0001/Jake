from core.nest_search import run_nest_search
from core.skill_result import SkillResult


class SemanticSearchFilesSkill:
    """Ricerca per significato nell'indice locale di NEST (v2.0: integrazione profonda con
    NEST). Trova file rilevanti anche senza corrispondenza letterale di parole, ma richiede
    che l'indice semantico sia gia' stato preparato (vedi BUILD_SEMANTIC_INDEX)."""

    metadata = {
        "intent": "SEMANTIC_SEARCH_FILES",
        "description": "Cerca file per significato (non per parole esatte) nell'indice semantico di NEST.",
        "parameters": {
            "query": {
                "type": "string",
                "required": True,
                "description": "Cosa cercare, descritto liberamente.",
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

        return run_nest_search(self.nest_client, self.conversation_state, query, self.nest_client.semantic_search)
