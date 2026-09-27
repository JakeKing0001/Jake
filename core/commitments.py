"""F6.4.1/F6.4.2: "devo mandare il preventivo entro venerdi'" e' un impegno. Jake lo riconosce e PROPONE un
promemoria - mai una memoria o un promemoria creati da soli: la decisione resta all'utente ("si'" / "no").

Solo la forma esplicita "<devo | prometto di | mi sono impegnato a ...> <cosa> entro <giorno>": una frase che non
dice entro quando non e' un impegno con scadenza e non genera domande (niente interruzioni per ogni "devo")."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

_WEEKDAYS = {"lunedi": 0, "martedi": 1, "mercoledi": 2, "giovedi": 3, "venerdi": 4, "sabato": 5, "domenica": 6}
_PATTERN = re.compile(
    r"^\s*(?:io\s+)?(?:devo|dovrei|prometto\s+di|ho\s+promesso\s+di|mi\s+(?:sono\s+)?impegnat[oa]\s+a|mi\s+impegno\s+a)\s+"
    r"(?P<what>.{3,120}?)\s+entro\s+(?P<when>oggi|stasera|domani|dopodomani|" + "|".join(_WEEKDAYS) + r")\b",
)
MORNING_HOUR = 9  # il promemoria arriva la mattina del giorno di scadenza, non all'ultimo minuto
EVENING_HOUR = 18


@dataclass(frozen=True)
class Commitment:
    what: str
    deadline: str  # come l'ha detta l'utente: "venerdi", "domani"...
    remind_at: datetime


def _plain(text: str) -> str:
    """Minuscole e accenti tolti carattere per carattere: stessa lunghezza, cosi' gli indici del match valgono anche
    sul testo originale (il promemoria conserva maiuscole e accenti dell'utente)."""
    text = text.lower()
    for accented, plain in (("ì", "i"), ("è", "e"), ("é", "e"), ("à", "a"), ("ò", "o"), ("ù", "u"), ("’", "'")):
        text = text.replace(accented, plain)
    return text


def detect_commitment(text: str, now: datetime) -> Commitment | None:
    plain = _plain(text)
    match = _PATTERN.match(plain)
    if not match:
        return None
    source = text if len(plain) == len(text) else plain
    what = " ".join(source[match.start("what"):match.end("what")].split())
    when = match.group("when")
    today = now.replace(second=0, microsecond=0)
    if when in ("oggi", "stasera"):
        remind_at = today.replace(hour=EVENING_HOUR - 1 if when == "oggi" else EVENING_HOUR, minute=0)
    else:
        if when in _WEEKDAYS:
            days = (_WEEKDAYS[when] - now.weekday()) % 7 or 7  # "entro venerdi'" detto di venerdi' = il prossimo
        else:
            days = 1 if when == "domani" else 2
        remind_at = (today + timedelta(days=days)).replace(hour=MORNING_HOUR, minute=0)
    if remind_at <= now + timedelta(minutes=15):
        remind_at = today + timedelta(minutes=30)  # scadenza troppo vicina: tra mezz'ora, non nel passato
    return Commitment(what, when, remind_at)


def describe(remind_at: datetime, now: datetime) -> str:
    """"domani alle 9", "venerdi alle 9", "alle 17" - come Jake lo dice nella domanda."""
    days = (remind_at.date() - now.date()).days
    hour = f"alle {remind_at.hour}" + (f":{remind_at.minute:02d}" if remind_at.minute else "")
    if days == 0:
        return hour
    if days == 1:
        return f"domani {hour}"
    names = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    return f"{names[remind_at.weekday()]} {hour}"
