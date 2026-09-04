import json
from urllib import error, parse, request

from core.network import is_online
from core.skill_result import SkillResult


class WebSearchSkill:
    """Cerca sul web tramite l'API Instant Answer di DuckDuckGo (nessuna chiave richiesta).

    Nota: restituisce solo risposte "istantanee" strutturate (voci enciclopediche, definizioni,
    argomenti noti), non un elenco completo di risultati di ricerca come un motore tradizionale."""

    metadata = {
        "intent": "WEB_SEARCH",
        "description": "Cerca sul web una risposta rapida a un argomento noto (non un elenco di risultati completo).",
        "remote": True,
        "parameters": {
            "query": {
                "type": "string",
                "required": True,
                "description": "Cosa cercare sul web.",
            },
        },
    }

    def __init__(self, timeout: float = 8):
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        query = (parameters.get("query") or "").strip()
        if not query:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if not is_online():
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")

        params = parse.urlencode({
            "q": query, "format": "json", "no_html": "1", "no_redirect": "1", "skip_disambig": "1",
        })
        url = f"https://api.duckduckgo.com/?{params}"

        try:
            with request.urlopen(url, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return SkillResult(success=False, data={"query": query}, error="NETWORK_UNAVAILABLE")

        summary = payload.get("AbstractText") or ""
        source_url = payload.get("AbstractURL") or ""
        if not summary:
            for topic in payload.get("RelatedTopics", []):
                if isinstance(topic, dict) and topic.get("Text"):
                    summary = topic["Text"]
                    source_url = topic.get("FirstURL", "")
                    break

        if not summary:
            return SkillResult(success=False, data={"query": query}, error="NOT_FOUND")

        return SkillResult(success=True, data={"query": query, "summary": summary, "url": source_url})
