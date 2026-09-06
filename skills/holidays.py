from datetime import date, timedelta

from core.skill_result import SkillResult


def _easter_sunday(year: int) -> date:
    """Algoritmo di Gauss per calcolare la data della Pasqua (necessario perche' Pasqua e
    Pasquetta, a differenza delle altre festivita', cambiano data ogni anno)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _italian_holidays(year: int) -> dict[date, str]:
    easter = _easter_sunday(year)
    return {
        date(year, 1, 1): "Capodanno",
        date(year, 1, 6): "Epifania",
        easter: "Pasqua",
        easter + timedelta(days=1): "Pasquetta",
        date(year, 4, 25): "Festa della Liberazione",
        date(year, 5, 1): "Festa dei Lavoratori",
        date(year, 6, 2): "Festa della Repubblica",
        date(year, 8, 15): "Ferragosto",
        date(year, 11, 1): "Ognissanti",
        date(year, 12, 8): "Immacolata Concezione",
        date(year, 12, 25): "Natale",
        date(year, 12, 26): "Santo Stefano",
    }


class GetNextHolidaySkill:
    metadata = {
        "intent": "GET_NEXT_HOLIDAY",
        "description": "Restituisce la prossima festivita' nazionale italiana e quanti giorni mancano.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        today = date.today()
        holidays = _italian_holidays(today.year)
        holidays.update(_italian_holidays(today.year + 1))

        upcoming = min((d for d in holidays if d >= today), default=None)
        if upcoming is None:
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        return SkillResult(success=True, data={
            "name": holidays[upcoming], "date": upcoming.strftime("%d/%m/%Y"), "days_until": (upcoming - today).days,
        })
