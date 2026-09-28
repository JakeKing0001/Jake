import json
from urllib import error, parse

from core.network import is_online, read_url
from core.skill_result import SkillResult

# codici meteo WMO usati da Open-Meteo (https://open-meteo.com/en/docs): descrizione italiana breve
_WMO = {
    0: "sereno", 1: "prevalentemente sereno", 2: "parzialmente nuvoloso", 3: "coperto",
    45: "nebbia", 48: "nebbia con brina",
    51: "pioviggine leggera", 53: "pioviggine", 55: "pioviggine intensa",
    56: "pioviggine gelata", 57: "pioviggine gelata intensa",
    61: "pioggia debole", 63: "pioggia", 65: "pioggia forte",
    66: "pioggia gelata", 67: "pioggia gelata forte",
    71: "neve debole", 73: "neve", 75: "neve forte", 77: "nevischio",
    80: "rovesci deboli", 81: "rovesci", 82: "rovesci violenti",
    85: "rovesci di neve", 86: "forti rovesci di neve",
    95: "temporale", 96: "temporale con grandine", 99: "temporale con forte grandine",
}


class GetWeatherSkill:
    """Meteo attuale via Open-Meteo: nessuna chiave da configurare. Prima il nome della citta' diventa coordinate
    (geocoding di Open-Meteo), poi si leggono le condizioni attuali in quel punto."""

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

    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, config=None, timeout: float = 8):
        self.config = config
        self.timeout = timeout

    def _get(self, url: str, params: dict):
        payload = json.loads(read_url(f"{url}?{parse.urlencode(params)}", self.timeout).decode("utf-8"))
        return payload if isinstance(payload, dict) else None

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        city = (parameters.get("city") or "").strip()
        if not city:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if not is_online():
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")

        try:
            places = self._get(self.GEOCODING_URL, {"name": city, "count": 1, "language": "it", "format": "json"})
            results = places.get("results") if places else None
            place = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else None
            if place is None:
                # Open-Meteo risponde senza "results" quando il nome non esiste; un corpo non valido e' un guasto
                return SkillResult(success=False, data={"city": city},
                                   error="CITY_NOT_FOUND" if places is not None else "NETWORK_UNAVAILABLE")
            latitude, longitude = place.get("latitude"), place.get("longitude")
            if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
                return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")
            forecast = self._get(self.FORECAST_URL, {
                "latitude": latitude, "longitude": longitude, "timezone": "auto",
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m"})
        except (error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
            return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")

        current = forecast.get("current") if forecast else None
        if not isinstance(current, dict):
            return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")
        code = current.get("weather_code")
        return SkillResult(success=True, data={
            "city": place.get("name") or city,
            "description": _WMO.get(code, "") if isinstance(code, int) else "",
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "wind_kmh": current.get("wind_speed_10m"),
        })
