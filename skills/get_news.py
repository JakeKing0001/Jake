import json
from urllib import error, parse

from core.network import is_online, read_url
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
            payload = json.loads(read_url(url, self.timeout).decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return SkillResult(success=False, data={"topic": topic}, error="NETWORK_UNAVAILABLE")

        # F1: stesso buco sistemico corretto in questa sessione per altri consumatori diretti di
        # API esterne - un corpo JSON valido ma non nella forma attesa (non un dizionario, o
        # "articles" non una lista di dizionari) faceva sollevare AttributeError, mai catturato.
        if not isinstance(payload, dict):
            return SkillResult(success=False, data={"topic": topic}, error="NOT_FOUND")
        raw_articles = payload.get("articles")
        if not isinstance(raw_articles, list):
            return SkillResult(success=False, data={"topic": topic}, error="NOT_FOUND")

        articles = [article for article in raw_articles if isinstance(article, dict)][:self.MAX_RESULTS]
        if not articles:
            return SkillResult(success=False, data={"topic": topic}, error="NOT_FOUND")

        def _source_name(article: dict) -> str:
            source = article.get("source")
            return source.get("name", "") if isinstance(source, dict) else ""

        headlines = [
            {"title": article.get("title", ""), "source": _source_name(article)}
            for article in articles
        ]
        return SkillResult(success=True, data={"topic": topic, "headlines": headlines})
