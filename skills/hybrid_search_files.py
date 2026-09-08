from core.nest_search import run_nest_search
from core.skill_result import SkillResult


class HybridSearchFilesSkill:
    """Ricerca combinata (esatta + per significato) nell'indice locale di NEST (v4.5, NEST Deep
    Integration). NestClient.hybrid_search esisteva gia' ma nessuna skill lo richiamava: la
    scelta era sempre lasciata all'utente/al modello tra SEARCH_FILES (solo corrispondenza
    letterale) e SEMANTIC_SEARCH_FILES (solo significato, richiede l'indice semantico gia'
    pronto). Qui NEST prova entrambe le strade in una sola chiamata, il ripiego giusto quando
    non e' chiaro in anticipo se la query ha piu' bisogno dell'una o dell'altra."""

    metadata = {
        "intent": "HYBRID_SEARCH_FILES",
        "description": "Cerca file combinando corrispondenza esatta e ricerca per significato nell'indice "
        "locale di NEST in una sola query: usalo quando non e' chiaro se conviene una ricerca letterale o "
        "per significato, o quando SEARCH_FILES/SEMANTIC_SEARCH_FILES non hanno trovato nulla.",
        "parameters": {
            "query": {
                "type": "string",
                "required": True,
                "description": "Cosa cercare, per nome/contenuto o descritto liberamente.",
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

        return run_nest_search(self.nest_client, self.conversation_state, query, self.nest_client.hybrid_search)
