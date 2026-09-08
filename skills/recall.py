from core.skill_result import SkillResult


class RecallSkill:
    metadata = {
        "intent": "RECALL",
        "description": "Recupera informazioni precedentemente memorizzate, per chiave esatta o ricerca libera.",
        "parameters": {
            "key": {
                "type": "string",
                "required": False,
                "description": "Nome esatto dell'informazione da recuperare, se noto.",
            },
            "query": {
                "type": "string",
                "required": False,
                "description": (
                    "Testo libero da cercare, con le stesse parole usate dall'utente "
                    "(non tradurre e non parafrasare)."
                ),
            },
        },
    }

    SEMANTIC_SCORE_THRESHOLD = 0.5

    def __init__(self, memory_manager, embedding_provider=None):
        self.memory_manager = memory_manager
        self.embedding_provider = embedding_provider

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        key = (parameters.get("key") or "").strip()
        query = (parameters.get("query") or "").strip()

        results = self.memory_manager.recall(key=key or None, query=query or None)
        if not results and key and not query:
            # La corrispondenza esatta di 'key' e' fragile quando il testo arriva da un LLM
            # che puo' riformulare leggermente la chiave: ripiega su una ricerca libera.
            results = self.memory_manager.recall(query=key)
            query = query or key

        if not results and query and self.embedding_provider is not None:
            # Nessuna corrispondenza testuale: prova la ricerca semantica (per sinonimi o
            # frasi in lingue diverse, es. "ristorante" vs "restaurant").
            query_embedding = self.embedding_provider.embed(query)
            if query_embedding is not None:
                semantic_results = self.memory_manager.semantic_recall(query_embedding)
                results = [r for r in semantic_results if r["score"] >= self.SEMANTIC_SCORE_THRESHOLD]

        if not results:
            return SkillResult(success=False, data={"key": key, "query": query}, error="NOT_FOUND")

        # Un salto nel grafo di conoscenza personale (v4.4): se il ricordo trovato e' collegato
        # ad altri (LINK_MEMORY), li include, cosi' ricordare "Mario" richiama anche "lavora per
        # Acme" senza dover chiedere separatamente.
        for entry in results:
            related = self.memory_manager.related(entry["key"], entry.get("category", "fact"))
            if related:
                entry["related"] = related
        return SkillResult(success=True, data={"results": results})
