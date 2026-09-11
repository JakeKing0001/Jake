import base64
import json
from pathlib import Path
from urllib import error, request
from core.ollama_client import DEFAULT_BASE_URL

DEFAULT_PROMPT = (
    "Descrivi in italiano, in modo conciso, cosa vedi in questa schermata: layout, "
    "app aperte, testo importante, immagini o grafici presenti."
)


class VisionProvider:
    """Descrive lo screenshot con un modello multimodale locale via Ollama (v3.0: visione
    reale, non solo OCR). Stesso stile di EmbeddingProvider: nessuna eccezione esce da
    describe(), un fallimento (Ollama giu', modello di visione non scaricato) restituisce
    semplicemente None e la skill chiamante degrada con un messaggio comprensibile."""

    def __init__(self, model: str = "qwen2.5vl:7b", base_url: str | None = None, timeout: float = 60):
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def describe(self, image_path: Path, question: str | None = None) -> str | None:
        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError:
            return None
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 8192},
            "messages": [
                {"role": "user", "content": question or DEFAULT_PROMPT, "images": [image_b64]},
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
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return None

        # Riprodotto per davvero: un corpo JSON valido ma non nella forma attesa (es. Ollama
        # giu' dietro un proxy che risponde "null", o un cambio futuro dell'API) faceva uscire
        # un AttributeError da describe(), rompendo la promessa dichiarata sopra ("nessuna
        # eccezione esce da describe()") - la skill chiamante (skills/describe_screen.py) non
        # ha un try/except attorno a questa chiamata, si fida di quella promessa.
        if not isinstance(result, dict):
            return None
        message = result.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return content.strip() if isinstance(content, str) and content.strip() else None
