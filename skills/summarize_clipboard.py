import json
from urllib import error, request

from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL
from core.network import read_url

MAX_INPUT_CHARS = 6000


class SummarizeClipboardSkill:
    """Riassume il testo attualmente negli appunti (es. un articolo copiato dal browser) via Ollama."""

    metadata = {
        "intent": "SUMMARIZE_CLIPBOARD",
        "description": "Riassume il testo attualmente presente negli appunti. Usalo per richieste "
        "come 'riassumi quello che ho copiato', 'fammi un riassunto degli appunti'. Diverso da "
        "CLIPBOARD_READ, che legge il testo per intero senza riassumerlo.",
        "remote": True,
        "parameters": {},
    }

    def __init__(self, model: str = "qwen2.5:7b", base_url: str = None, timeout: float = 40):
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            try:
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return SkillResult(success=False, data={}, error="CLIPBOARD_EMPTY")

        if not text or not text.strip():
            return SkillResult(success=False, data={}, error="CLIPBOARD_EMPTY")

        summary = self._summarize(text[:MAX_INPUT_CHARS])
        if summary is None:
            return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")
        return SkillResult(success=True, data={"summary": summary})

    def _summarize(self, text: str) -> str | None:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 8192, "temperature": 0.3},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Riassumi in italiano, in massimo 5 frasi, il testo fornito dall'utente. "
                        "Rispondi solo con il riassunto, senza premesse ne' markdown."
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
            result = json.loads(read_url(http_request, self.timeout).decode("utf-8"))
            return result["message"]["content"].strip() or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
            # F1: stesso buco corretto in core/vision_provider.py/skills/ask_question.py in
            # questa sessione - un corpo JSON valido ma non nella forma attesa fa sollevare un
            # TypeError da questo indicizzamento, non un KeyError.
            return None
