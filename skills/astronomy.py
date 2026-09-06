import json
from datetime import date, datetime
from urllib import error, parse, request

from core.network import is_online
from core.skill_result import SkillResult

_MOON_PHASE_NAMES = [
    "luna nuova", "luna crescente", "primo quarto", "gibbosa crescente",
    "luna piena", "gibbosa calante", "ultimo quarto", "luna calante",
]
_SYNODIC_MONTH_DAYS = 29.53058867
_REFERENCE_NEW_MOON = datetime(2000, 1, 6, 18, 14)


class GetSunriseSunsetSkill:
    """Usa Open-Meteo (geocoding + previsioni), entrambe API gratuite senza chiave richiesta:
    nessuna configurazione aggiuntiva necessaria, a differenza di GET_WEATHER."""

    metadata = {
        "intent": "GET_SUNRISE_SUNSET",
        "description": "Restituisce gli orari di alba e tramonto di oggi per una citta'.",
        "remote": True,
        "parameters": {
            "city": {"type": "string", "required": True, "description": "Citta' di cui conoscere alba e tramonto."},
        },
    }

    def __init__(self, timeout: float = 8):
        self.timeout = timeout

    def _geocode(self, city: str):
        params = parse.urlencode({"name": city, "count": 1, "language": "it"})
        url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
        with request.urlopen(url, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        results = payload.get("results") or []
        return (results[0]["latitude"], results[0]["longitude"]) if results else None

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        city = (parameters.get("city") or "").strip()
        if not city:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if not is_online():
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")

        try:
            coordinates = self._geocode(city)
            if coordinates is None:
                return SkillResult(success=False, data={"city": city}, error="CITY_NOT_FOUND")

            latitude, longitude = coordinates
            params = parse.urlencode({
                "latitude": latitude, "longitude": longitude, "daily": "sunrise,sunset", "timezone": "auto",
            })
            url = f"https://api.open-meteo.com/v1/forecast?{params}"
            with request.urlopen(url, timeout=self.timeout) as response:
                forecast = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError):
            return SkillResult(success=False, data={"city": city}, error="NETWORK_UNAVAILABLE")

        sunrise = forecast["daily"]["sunrise"][0].split("T")[1]
        sunset = forecast["daily"]["sunset"][0].split("T")[1]
        return SkillResult(success=True, data={"city": city, "sunrise": sunrise, "sunset": sunset})


class GetMoonPhaseSkill:
    """Calcolo offline (nessuna chiamata di rete): approssimazione standard basata sul ciclo
    sinodico lunare medio, precisa entro circa un giorno. Sufficiente per un uso informativo."""

    metadata = {
        "intent": "GET_MOON_PHASE",
        "description": "Restituisce la fase lunare attuale.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        now = datetime.combine(date.today(), datetime.min.time())
        days_since = (now - _REFERENCE_NEW_MOON).total_seconds() / 86400
        phase_fraction = (days_since % _SYNODIC_MONTH_DAYS) / _SYNODIC_MONTH_DAYS
        index = round(phase_fraction * 8) % 8
        return SkillResult(success=True, data={"phase": _MOON_PHASE_NAMES[index]})
