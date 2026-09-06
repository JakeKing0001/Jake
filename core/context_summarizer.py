import json
from urllib import error, request
from core.ollama_client import DEFAULT_BASE_URL


class ContextSummarizer:
    """Riassume conversazioni lunghe in un breve paragrafo, usando Ollama (nessuno schema strutturato)."""

    def __init__(self, model: str = None, base_url: str = None, timeout: float = 30):
        self.model = model or "qwen2.5:7b"
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def summarize(self, turns: list[dict]) -> str | None:
        if not turns:
            return None

        conversation = "\n".join(f"{turn['role']}: {turn['text']}" for turn in turns)
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 8192, "temperature": 0.2},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Riassumi la conversazione seguente in italiano, in massimo 3 frasi, "
                        "mantenendo solo i fatti rilevanti da ricordare in futuro. "
                        "Rispondi solo con il riassunto, senza premesse."
                    ),
                },
                {"role": "user", "content": conversation},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result["message"]["content"].strip() or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
            return None
