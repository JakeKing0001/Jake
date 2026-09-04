import json
from urllib import error, parse, request

from core.network import is_online
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
            with request.urlopen(url, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            if exc.code == 404:
                return SkillResult(success=False, data={"city": city}, error="CITY_NOT_FOUND")
            return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")

        description = (payload.get("weather") or [{}])[0].get("description", "")
        temperature = payload.get("main", {}).get("temp")
        feels_like = payload.get("main", {}).get("feels_like")

        return SkillResult(success=True, data={
            "city": city,
            "description": description,
            "temperature": temperature,
            "feels_like": feels_like,
        })
