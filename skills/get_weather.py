import json
from urllib import error, parse

from core.network import is_online, read_url
from core.skill_result import SkillResult


class GetWeatherSkill:
    """Meteo attuale via OpenWeatherMap. Richiede una 'weather_api_key' in config/settings.json."""

    metadata = {
        "intent": "GET_WEATHER",
        "description": "Restituisce il meteo attuale per una citta'.",
        "remote": True,
        "parameters": {
            "city": {
                "type": "string",
                "required": True,
                "description": "Citta' di cui conoscere il meteo.",
            },
        },
    }

    def __init__(self, config, timeout: float = 8):
        self.config = config
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        city = (parameters.get("city") or "").strip()
        if not city:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        api_key = self.config.get("weather_api_key")
        if not api_key:
            return SkillResult(success=False, data={"setting": "weather_api_key"}, error="MISSING_API_KEY")

        if not is_online():
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")

        params = parse.urlencode({"q": city, "appid": api_key, "units": "metric", "lang": "it"})
        url = f"https://api.openweathermap.org/data/2.5/weather?{params}"

        try:
            payload = json.loads(read_url(url, self.timeout).decode("utf-8"))
        except error.HTTPError as exc:
            if exc.code == 404:
                return SkillResult(success=False, data={"city": city}, error="CITY_NOT_FOUND")
            return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")

        # F1: stesso buco sistemico corretto in questa sessione per altri consumatori diretti di
        # API esterne - un corpo JSON valido ma non nella forma attesa (payload/"weather"/"main"
        # non del tipo giusto) faceva sollevare AttributeError o TypeError, mai catturato.
        if not isinstance(payload, dict):
            return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")

        weather_entries = payload.get("weather")
        first_weather_entry = weather_entries[0] if isinstance(weather_entries, list) and weather_entries else {}
        description = first_weather_entry.get("description", "") if isinstance(first_weather_entry, dict) else ""

        main = payload.get("main")
        main = main if isinstance(main, dict) else {}
        temperature = main.get("temp")
        feels_like = main.get("feels_like")

        return SkillResult(success=True, data={
            "city": city,
            "description": description,
            "temperature": temperature,
            "feels_like": feels_like,
        })
