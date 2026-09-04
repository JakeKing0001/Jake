import json
from urllib import error, parse, request

from core.network import is_online
from core.skill_result import SkillResult


class GetNewsSkill:
    """Ultime notizie via NewsAPI.org. Richiede una 'news_api_key' in config/settings.json."""

    metadata = {
        "intent": "GET_NEWS",
        "description": "Restituisce le ultime notizie principali, opzionalmente su un argomento.",
        "remote": True,
        "parameters": {
            "topic": {
                "type": "string",
                "required": False,
                "description": "Argomento delle notizie. Se omesso: prime notizie in Italia.",
            },
        },
    }

    MAX_RESULTS = 5

    def __init__(self, config, timeout: float = 8):
        self.config = config
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        topic = (parameters.get("topic") or "").strip()

        api_key = self.config.get("news_api_key")
        if not api_key:
            return SkillResult(success=False, data={"setting": "news_api_key"}, error="MISSING_API_KEY")

        if not is_online():
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")

        if topic:
            params = parse.urlencode({"q": topic, "language": "it", "sortBy": "publishedAt", "apiKey": api_key})
            url = f"https://newsapi.org/v2/everything?{params}"
        else:
            params = parse.urlencode({"country": "it", "apiKey": api_key})
            url = f"https://newsapi.org/v2/top-headlines?{params}"

        try:
            with request.urlopen(url, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return SkillResult(success=False, data={"topic": topic}, error="NETWORK_UNAVAILABLE")

        articles = payload.get("articles", [])[:self.MAX_RESULTS]
        if not articles:
            return SkillResult(success=False, data={"topic": topic}, error="NOT_FOUND")

        headlines = [
            {"title": article.get("title", ""), "source": (article.get("source") or {}).get("name", "")}
            for article in articles
        ]
        return SkillResult(success=True, data={"topic": topic, "headlines": headlines})
