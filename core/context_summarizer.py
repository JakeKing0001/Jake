import json
from urllib import error, request


class ContextSummarizer:
    """Riassume conversazioni lunghe in un breve paragrafo, usando Ollama (nessuno schema strutturato)."""

    def __init__(self, model: str = None, base_url: str = "http://localhost:11434", timeout: float = 30):
        self.model = model or "qwen2.5:7b"
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def summarize(self, turns: list[dict]) -> str | None:
        if not turns:
            return None

        conversation = "\n".join(f"{turn['role']}: {turn['text']}" for turn in turns)
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.2},
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
