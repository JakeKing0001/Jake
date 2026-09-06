import json
from urllib import error, request

from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL


class TranslateTextSkill:
    """Traduzione offline (nessuna chiamata di rete esterna) via Ollama, stesso motore usato
    per l'intent classifier: nessuna dipendenza o API key aggiuntiva da configurare."""

    metadata = {
        "intent": "TRANSLATE_TEXT",
        "description": "Traduce un testo in un'altra lingua.",
        "remote": True,
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Il testo da tradurre.",
            },
            "target_language": {
                "type": "string",
                "required": True,
                "description": "Lingua di destinazione, es. 'inglese', 'spagnolo', 'francese'.",
            },
        },
    }

    def __init__(self, model: str = "qwen2.5:7b", base_url: str = None, timeout: float = 30):
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        target_language = (parameters.get("target_language") or "").strip()
        if not text or not target_language:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        translation = self._translate(text, target_language)
        if translation is None:
            return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")
        return SkillResult(success=True, data={"text": text, "target_language": target_language, "translation": translation})

    def _translate(self, text: str, target_language: str) -> str | None:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 8192, "temperature": 0},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Traduci il testo dell'utente in {target_language}. Rispondi SOLO con "
                        "la traduzione, senza spiegazioni, premesse o virgolette."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result["message"]["content"].strip() or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
            return None
