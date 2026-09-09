"""Parsing del linguaggio naturale per intervalli di tempo (F5, Memory 2.0 - "linguaggio
naturale per il tempo" in ROADMAP.md, fase F5).

Restituisce un intervallo (since, until) in ISO 8601, compatibile con
MemoryManager.recall(since=..., until=...) (v3.4: gia' esistente, confrontato
lessicograficamente perche' ISO 8601 e' ordinabile). Copre solo espressioni RELATIVE a 'adesso'
("ieri", "questa settimana", "negli ultimi 3 giorni"...): riferimenti che richiedono di
conoscere un evento esterno ("prima della riunione", "quando lavoravo a X") restano fuori -
servirebbe incrociare calendario/contesto, non solo il testo, dichiarato esplicitamente non
affrontato qui (vedi ROADMAP.md, F5).

Non riusa skills/datetime_utils.py::parse_spoken_date: quella lavora su date.today() (non
iniettabile, pensata per un singolo giorno puntuale, es. GET_DAY_OF_WEEK), qui serve un
INTERVALLO con un 'adesso' iniettabile per i test - due bisogni diversi, non la stessa funzione
con un parametro in piu'."""
import re
from datetime import date, datetime, timedelta, timezone

_SINGLE_DAY_OFFSETS = {
    "oggi": 0, "stamattina": 0, "stasera": 0, "stanotte": 0, "questa mattina": 0,
    "ieri": -1, "l'altro ieri": -2, "avantieri": -2,
    "domani": 1, "dopodomani": 2,
}

_RELATIVE_DAYS_RE = re.compile(r"^(?:negli |gli )?ultim[ei] (\d+) giorni$")
_RELATIVE_HOURS_RE = re.compile(r"^(?:nell[e'] |le )?ultim[ei] (\d+) or[ae]$")


def _day_bounds(day: date, tz: timezone) -> tuple[str, str]:
    """(inizio, fine) del giorno indicato, come intervallo mezzo-aperto [00:00, 00:00 del
    giorno dopo) - coerente con recall(since=..., until=...) che confronta stringhe ISO."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _week_start(day: date) -> date:
    """Lunedi' della settimana che contiene 'day' (convenzione italiana, non domenica)."""
    return day - timedelta(days=day.weekday())


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _add_months(day: date, delta: int) -> date:
    month_index = day.month - 1 + delta
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def parse_relative_range(text: str, now: datetime = None) -> tuple[str, str] | None:
    """Restituisce (since, until) ISO 8601 per un'espressione temporale relativa in italiano, o
    None se il testo non corrisponde a nessuna espressione riconosciuta - MAI un'eccezione: chi
    chiama tratta None come 'nessun vincolo temporale riconosciuto', non come un errore.

    now e' iniettabile (default datetime.now(timezone.utc)) per rendere i test deterministici
    senza dipendere dalla data reale del giorno in cui girano."""
    now = now if now is not None else datetime.now(timezone.utc)
    tz = now.tzinfo or timezone.utc
    text = (text or "").strip().lower().replace("’", "'")
    if not text:
        return None

    today = now.date()

    if text in _SINGLE_DAY_OFFSETS:
        return _day_bounds(today + timedelta(days=_SINGLE_DAY_OFFSETS[text]), tz)

    days_match = _RELATIVE_DAYS_RE.match(text)
    if days_match:
        return (now - timedelta(days=int(days_match.group(1)))).isoformat(), now.isoformat()

    hours_match = _RELATIVE_HOURS_RE.match(text)
    if hours_match:
        return (now - timedelta(hours=int(hours_match.group(1)))).isoformat(), now.isoformat()

    if text in ("questa settimana", "questa settimana scorsa"):
        start = datetime(*_week_start(today).timetuple()[:3], tzinfo=tz)
        return start.isoformat(), now.isoformat()
    if text in ("la settimana scorsa", "settimana scorsa"):
        this_week_start = _week_start(today)
        last_week_start = this_week_start - timedelta(days=7)
        start = datetime(*last_week_start.timetuple()[:3], tzinfo=tz)
        end = datetime(*this_week_start.timetuple()[:3], tzinfo=tz)
        return start.isoformat(), end.isoformat()

    if text in ("questo mese",):
        start = datetime(*_month_start(today).timetuple()[:3], tzinfo=tz)
        return start.isoformat(), now.isoformat()
    if text in ("il mese scorso", "mese scorso"):
        this_month_start = _month_start(today)
        last_month_start = _add_months(this_month_start, -1)
        start = datetime(*last_month_start.timetuple()[:3], tzinfo=tz)
        end = datetime(*this_month_start.timetuple()[:3], tzinfo=tz)
        return start.isoformat(), end.isoformat()

    if text in ("quest'anno", "questo anno"):
        start = datetime(today.year, 1, 1, tzinfo=tz)
        return start.isoformat(), now.isoformat()
    if text in ("l'anno scorso", "anno scorso"):
        start = datetime(today.year - 1, 1, 1, tzinfo=tz)
        end = datetime(today.year, 1, 1, tzinfo=tz)
        return start.isoformat(), end.isoformat()

    return None
