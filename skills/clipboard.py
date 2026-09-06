import json
from urllib import error, request

from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL


class ClipboardReadSkill:
    metadata = {
        "intent": "CLIPBOARD_READ",
        "description": "Legge il testo attualmente negli appunti.",
        "parameters": {},
    }

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

        if not text:
            return SkillResult(success=False, data={}, error="CLIPBOARD_EMPTY")
        return SkillResult(success=True, data={"text": text})


class ClipboardWriteSkill:
    metadata = {
        "intent": "CLIPBOARD_WRITE",
        "description": "Scrive un testo negli appunti.",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Testo da copiare negli appunti.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = parameters.get("text") or ""
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return SkillResult(success=False, data={"text": text}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"text": text})


class TranslateClipboardSkill:
    """Legge gli appunti e traduce direttamente, senza dover prima passare per CLIPBOARD_READ
    e poi ridettare il testo a TRANSLATE_TEXT."""

    metadata = {
        "intent": "TRANSLATE_CLIPBOARD",
        "description": "Traduce il testo attualmente negli appunti in un'altra lingua.",
        "remote": True,
        "parameters": {
            "target_language": {
                "type": "string",
                "required": True,
                "description": "Lingua di destinazione, es. 'inglese', 'spagnolo'.",
            },
        },
    }

    def __init__(self, model: str = "qwen2.5:7b", base_url: str = None, timeout: float = 30):
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        import win32clipboard

        parameters = parameters or {}
        target_language = (parameters.get("target_language") or "").strip()
        if not target_language:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

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

        translation = self._translate(text[:4000], target_language)
        if translation is None:
            return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")
        return SkillResult(success=True, data={"target_language": target_language, "translation": translation})

    def _translate(self, text: str, target_language: str) -> str | None:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 8192, "temperature": 0},
            "messages": [
                {
                    "role": "system",
                    "content": f"Traduci il testo dell'utente in {target_language}. Rispondi SOLO con la traduzione.",
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
