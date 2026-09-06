from datetime import date, datetime
from zoneinfo import ZoneInfo, available_timezones

from core.skill_result import SkillResult

_WEEKDAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]

_TIMEZONE_ALIASES = {
    "italia": "Europe/Rome", "roma": "Europe/Rome", "londra": "Europe/London", "regno unito": "Europe/London",
    "new york": "America/New_York", "los angeles": "America/Los_Angeles", "tokyo": "Asia/Tokyo",
    "pechino": "Asia/Shanghai", "sydney": "Australia/Sydney", "parigi": "Europe/Paris",
    "berlino": "Europe/Berlin", "madrid": "Europe/Madrid", "mosca": "Europe/Moscow", "dubai": "Asia/Dubai",
    "utc": "UTC",
}


def _resolve_timezone(name: str) -> str | None:
    key = name.strip().lower()
    if key in _TIMEZONE_ALIASES:
        return _TIMEZONE_ALIASES[key]
    if name in available_timezones():
        return name
    return None


_HOLIDAYS = {
    "natale": (25, 12), "capodanno": (1, 1), "epifania": (6, 1), "befana": (6, 1), "ferragosto": (15, 8),
    "halloween": (31, 10), "san valentino": (14, 2), "festa della liberazione": (25, 4), "festa dei lavoratori": (1, 5),
    "primo maggio": (1, 5), "festa della repubblica": (2, 6), "ognissanti": (1, 11), "immacolata": (8, 12),
    "santo stefano": (26, 12), "vigilia di natale": (24, 12), "vigilia": (24, 12), "san silvestro": (31, 12),
}
_WEEKDAY_NAMES = {name: index for index, name in enumerate(_WEEKDAYS_IT)}
_WEEKDAY_NAMES.update({"lunedi": 0, "martedi": 1, "mercoledi": 2, "giovedi": 3, "venerdi": 4})


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    month = (h + m - 7 * ((a + 11 * h + 22 * m) // 451) + 114) // 31
    day = ((h + m - 7 * ((a + 11 * h + 22 * m) // 451) + 114) % 31) + 1
    return date(year, month, day)


def parse_spoken_date(raw: str, future: bool = False) -> date | None:
    """'GG/MM/AAAA', 'GG/MM' (prossima occorrenza), 'oggi/domani/dopodomani/ieri', un giorno della
    settimana ('venerdi' = il prossimo), una festivita' ('natale', 'pasqua'). None se non capita."""
    from datetime import timedelta

    text = (raw or "").strip().lower().replace("’", "'")
    if not text:
        return None
    today = date.today()
    relative = {"oggi": 0, "domani": 1, "dopodomani": 2, "ieri": -1, "l'altro ieri": -2, "stasera": 0, "stanotte": 0}
    if text in relative:
        return today + timedelta(days=relative[text])
    for prefix in ("il ", "a ", "al ", "per ", "di "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text in _WEEKDAY_NAMES:
        delta = (_WEEKDAY_NAMES[text] - today.weekday()) % 7
        return today + timedelta(days=delta or 7)
    if text.startswith("pasqua"):
        easter = _easter(today.year)
        if easter < today:
            easter = _easter(today.year + 1)
        return easter + timedelta(days=1) if "pasquetta" in text else easter
    if text == "pasquetta":
        easter = _easter(today.year)
        if easter + timedelta(days=1) < today:
            easter = _easter(today.year + 1)
        return easter + timedelta(days=1)
    if text in _HOLIDAYS:
        day, month = _HOLIDAYS[text]
        candidate = date(today.year, month, day)
        if candidate < today:
            candidate = date(today.year + 1, month, day)
        return candidate
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        parsed = datetime.strptime(text, "%d/%m").date().replace(year=today.year)
        if future and parsed < today:
            parsed = parsed.replace(year=today.year + 1)
        return parsed
    except ValueError:
        pass
    return None


class GetDayOfWeekSkill:
    metadata = {
        "intent": "GET_DAY_OF_WEEK",
        "description": "Restituisce il giorno della settimana per una data (oggi, domani, una data 'GG/MM/AAAA', una festivita').",
        "parameters": {
            "date": {"type": "string", "required": False, "description": "Data 'GG/MM/AAAA', oppure 'oggi', 'domani', 'natale'... Se omessa usa oggi."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_date = (parameters.get("date") or "").strip()
        target = parse_spoken_date(raw_date) if raw_date else date.today()
        if target is None:
            return SkillResult(success=False, data={"date": raw_date}, error="INVALID_DATE")
        return SkillResult(success=True, data={"date": target.strftime("%d/%m/%Y"), "weekday": _WEEKDAYS_IT[target.weekday()]})


class DaysUntilSkill:
    metadata = {
        "intent": "DAYS_UNTIL",
        "description": "Calcola quanti giorni mancano a una data futura, a una festivita' (natale, capodanno, "
        "pasqua, ferragosto...) o a un giorno della settimana.",
        "parameters": {
            "date": {"type": "string", "required": True, "description": "Data 'GG/MM/AAAA' o 'GG/MM', oppure il nome della festivita'/giorno cosi' come detto (es. 'natale', 'venerdi')."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_date = (parameters.get("date") or "").strip()
        if not raw_date:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        target = parse_spoken_date(raw_date, future=True)
        if target is None:
            return SkillResult(success=False, data={"date": raw_date}, error="INVALID_DATE")

        days = (target - date.today()).days
        return SkillResult(success=True, data={"date": target.strftime("%d/%m/%Y"), "days": days, "label": raw_date})


class GetWeekNumberSkill:
    metadata = {
        "intent": "GET_WEEK_NUMBER",
        "description": "Restituisce il numero della settimana dell'anno corrente (o di una data data).",
        "parameters": {
            "date": {"type": "string", "required": False, "description": "Data in formato 'GG/MM/AAAA'. Se omessa usa oggi."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_date = (parameters.get("date") or "").strip()
        if raw_date:
            try:
                target = datetime.strptime(raw_date, "%d/%m/%Y").date()
            except ValueError:
                return SkillResult(success=False, data={"date": raw_date}, error="INVALID_DATE")
        else:
            target = date.today()

        return SkillResult(success=True, data={"week_number": target.isocalendar()[1]})


class ConvertTimezoneSkill:
    metadata = {
        "intent": "CONVERT_TIMEZONE",
        "description": "Converte un orario da un fuso orario a un altro.",
        "parameters": {
            "time": {"type": "string", "required": True, "description": "Orario 'HH:MM' da convertire."},
            "from_zone": {"type": "string", "required": True, "description": "Fuso o citta' di partenza, es. 'Italia', 'New York'."},
            "to_zone": {"type": "string", "required": True, "description": "Fuso o citta' di destinazione."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_time = (parameters.get("time") or "").strip()
        from_zone = _resolve_timezone(parameters.get("from_zone") or "")
        to_zone = _resolve_timezone(parameters.get("to_zone") or "")

        if not raw_time or from_zone is None or to_zone is None:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            hour, minute = (int(part) for part in raw_time.split(":"))
        except ValueError:
            return SkillResult(success=False, data={"time": raw_time}, error="INVALID_TIME")

        today = date.today()
        source = datetime(today.year, today.month, today.day, hour, minute, tzinfo=ZoneInfo(from_zone))
        converted = source.astimezone(ZoneInfo(to_zone))

        return SkillResult(success=True, data={"result": converted.strftime("%H:%M"), "to_zone": to_zone})
