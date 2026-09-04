from core.nest_client import NestError
from core.skill_result import SkillResult


class BuildSemanticIndexSkill:
    """Prepara/aggiorna l'indice semantico di NEST, richiesto da SEMANTIC_SEARCH_FILES.
    Puo' richiedere tempo su molte sorgenti: e' un'operazione esplicita, non automatica."""

    metadata = {
        "intent": "BUILD_SEMANTIC_INDEX",
        "description": "Prepara o aggiorna l'indice semantico locale di NEST (necessario per la ricerca per significato).",
        "parameters": {},
    }

    def __init__(self, nest_client):
        self.nest_client = nest_client

    def execute(self, parameters: dict = None):
        if not self.nest_client.is_available():
            return SkillResult(success=False, data={}, error="NEST_UNAVAILABLE")

        try:
            summary = self.nest_client.build_semantic_index()
        except NestError as exc:
            return SkillResult(success=False, data={"message": str(exc)}, error="NEST_ERROR")

        return SkillResult(success=True, data={"summary": summary})
