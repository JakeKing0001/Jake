import json
from urllib import error, parse

from core.network import is_online, read_url
from core.skill_result import SkillResult


class GetNewsSkill:
    """Ultime notizie via NewsData.io. Richiede una 'news_api_key' in config/settings.json (registrazione gratuita su
    https://newsdata.io, la chiave e' nella dashboard)."""

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
    URL = "https://newsdata.io/api/1/latest"

    def __init__(self, config, timeout: float = 8):
        self.config = config
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        topic = (parameters.get("topic") or "").strip()

        api_key = self.config.get("news_api_key")
        if not api_key:
            return SkillResult(success=False, data={"setting": "news_api_key", "signup": "https://newsdata.io"},
                               error="MISSING_API_KEY")

        if not is_online():
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")

        query = {"apikey": api_key, "language": "it"}
        if topic:
            query["q"] = topic
        else:
            query["country"] = "it"
        url = f"{self.URL}?{parse.urlencode(query)}"

        try:
            payload = json.loads(read_url(url, self.timeout).decode("utf-8"))
        except error.HTTPError as exc:
            if exc.code in (401, 403):  # chiave sbagliata o revocata: dirlo, non "rete non disponibile"
                return SkillResult(success=False, data={"setting": "news_api_key", "invalid": True,
                                                        "signup": "https://newsdata.io"}, error="MISSING_API_KEY")
            return SkillResult(success=False, data={"topic": topic}, error="NETWORK_UNAVAILABLE")
        except (error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
            return SkillResult(success=False, data={"topic": topic}, error="NETWORK_UNAVAILABLE")

        # F1: un corpo JSON valido ma non nella forma attesa (non un dizionario, o "results" non una lista di
        # dizionari) degrada a NOT_FOUND invece di sollevare AttributeError.
        if not isinstance(payload, dict):
            return SkillResult(success=False, data={"topic": topic}, error="NOT_FOUND")
        raw_articles = payload.get("results")
        if not isinstance(raw_articles, list):
            return SkillResult(success=False, data={"topic": topic}, error="NOT_FOUND")

        articles, seen = [], set()
        for article in raw_articles:
            title = article.get("title") if isinstance(article, dict) else None
            # NewsData ripete la stessa notizia da testate diverse: un titolo una volta sola
            if isinstance(title, str) and title.strip() and title.strip().lower() not in seen:
                seen.add(title.strip().lower())
                articles.append(article)
        if not articles:
            return SkillResult(success=False, data={"topic": topic}, error="NOT_FOUND")

        def _source_name(article: dict) -> str:
            source = article.get("source_name") or article.get("source_id")
            return source if isinstance(source, str) else ""

        headlines = [{"title": article["title"].strip(), "source": _source_name(article)}
                     for article in articles[:self.MAX_RESULTS]]
        return SkillResult(success=True, data={"topic": topic, "headlines": headlines})
