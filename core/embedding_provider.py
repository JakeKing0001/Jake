import json
from urllib import error, request
from core.ollama_client import DEFAULT_BASE_URL


class EmbeddingProvider:
    """Genera embedding testuali in locale via Ollama (nessuna chiamata cloud).

    Richiede un modello di embedding scaricato (`ollama pull nomic-embed-text`). Se Ollama o
    il modello non sono disponibili, embed() restituisce None: i chiamanti devono degradare
    con grazia alla ricerca testuale semplice, senza far fallire l'intera funzione."""

    def __init__(self, model: str = "nomic-embed-text", base_url: str | None = None, timeout: float = 15):
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def embed(self, text: str) -> list[float] | None:
        payload = {"model": self.model, "input": text}
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return None

        embeddings = payload.get("embeddings")
        if not embeddings or not isinstance(embeddings, list) or not isinstance(embeddings[0], list):
            return None
        return embeddings[0]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
