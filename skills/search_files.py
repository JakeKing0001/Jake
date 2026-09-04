from core.nest_client import NestError
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

        if not self.nest_client.is_available():
            return SkillResult(success=False, data={}, error="NEST_UNAVAILABLE")

        try:
            results = self.nest_client.search(query)
        except NestError as exc:
            return SkillResult(success=False, data={"message": str(exc)}, error="NEST_ERROR")

        if not results:
            return SkillResult(success=False, data={"query": query}, error="NOT_FOUND")

        structured_results = [
            {"path": result.path, "snippet": result.snippet, "score": result.score}
            for result in results
        ]
        self.conversation_state.set_last_search_results(structured_results)

        return SkillResult(success=True, data={"query": query, "results": structured_results})
