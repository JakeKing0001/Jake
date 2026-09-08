"""Logica condivisa dalle skill di ricerca file su NEST (SEARCH_FILES, SEMANTIC_SEARCH_FILES,
HYBRID_SEARCH_FILES, v4.5 NEST Deep Integration): stessa gestione di disponibilita'/errori/
risultati, l'unica differenza tra le tre e' quale comando NEST viene invocato."""
from core.nest_client import NestError
from core.skill_result import SkillResult


def run_nest_search(nest_client, conversation_state, query: str, search_method) -> SkillResult:
    if not nest_client.is_available():
        return SkillResult(success=False, data={}, error="NEST_UNAVAILABLE")

    try:
        results = search_method(query)
    except NestError as exc:
        return SkillResult(success=False, data={"message": str(exc)}, error="NEST_ERROR")

    if not results:
        return SkillResult(success=False, data={"query": query}, error="NOT_FOUND")

    structured_results = [
        {"path": result.path, "snippet": result.snippet, "score": result.score}
        for result in results
    ]
    conversation_state.set_last_search_results(structured_results)
    return SkillResult(success=True, data={"query": query, "results": structured_results})
