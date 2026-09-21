"""Brief giornaliero senza dati inventati (F6.6.1, F6.6.2, F6.6.3, F6.6.7).

Un riepilogo della giornata e' utile solo se ci si puo' fidare che ogni riga viene da qualche parte. La
regola che regge tutto il modulo e' quindi una sola: **un elemento entra nel brief solo se dichiara la sua
fonte e quando e' stato letto**. Niente riempitivo ("oggi e' una bella giornata"), niente sezione
inventata quando una fonte non risponde: si dice che quella fonte non ha risposto.

- `BriefSource` e' l'interfaccia di una fonte (agenda, scadenze, meteo, casa, attivita' aperte). Una fonte
  che solleva un errore o non e' collegata non rompe il brief: finisce in `unavailable` con il motivo.
  Le fonti reali dei connettori (calendario, posta) non esistono ancora e dipendono da F7; qui ci sono gli
  adattatori per quelle locali (promemoria, todo) e il contratto per le altre.
- ogni `BriefItem` ha `fetched_at` e `max_age_s`: un dato vecchio si mostra come vecchio (dettagliato) o si
  omette dichiarando quanti (breve); un elemento dal futuro o senza fonte e' scartato e contato in `rejected`;
- tre formati: `short` (poche righe, senza dettagli di freschezza salvo l'avviso sui dati vecchi),
  `detailed` (tutto, con fonte e eta') e `silent` (solo testo per lo schermo, niente voce);
- i dati di persone e organizzazioni si possono redigere in modo coerente (`Redactor`): la stessa persona
  ha sempre la stessa etichetta nel brief, cosi' il testo resta comprensibile senza rivelare chi e'."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

SECTION_ORDER = ["agenda", "deadlines", "open_tasks", "weather", "travel", "home", "other"]
SECTION_LABELS = {
    "agenda": "Agenda", "deadlines": "Scadenze", "open_tasks": "Attivita' aperte", "weather": "Meteo",
    "travel": "Viaggio", "home": "Casa", "other": "Altro",
}
SHORT_LIMIT_PER_SECTION = 3
SENSITIVITY = ("public", "personal", "private")


@dataclass(frozen=True)
class BriefItem:
    section: str
    text: str
    source: str
    fetched_at: float
    max_age_s: float = 3600.0
    sensitivity: str = "personal"
    priority: int = 0  # piu' alto = piu' importante, dentro la sezione
    people: tuple[str, ...] = ()  # nomi di persone citati (per la redazione)
    organizations: tuple[str, ...] = ()


class BriefSource(Protocol):
    name: str

    def fetch(self, now: float) -> list[BriefItem]:
        ...


@dataclass(frozen=True)
class RenderedItem:
    item: BriefItem
    text: str
    age_s: float
    stale: bool


@dataclass
class Brief:
    format: str
    generated_at: float
    sections: dict[str, list[RenderedItem]]
    unavailable: list[tuple[str, str]]  # (fonte, motivo)
    rejected: list[tuple[str, str]]  # (testo, motivo): scartati perche' senza provenienza valida
    omitted_stale: int
    text: str
    spoken_text: str | None

    def provenance(self) -> list[dict]:
        """Ogni riga del brief con la sua fonte e il suo momento di lettura: cio' che permette di verificarla."""
        return [
            {"section": section, "text": rendered.text, "source": rendered.item.source, "fetched_at": rendered.item.fetched_at}
            for section, items in self.sections.items() for rendered in items
        ]


# ---- redazione (F6.6.7) --------------------------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.)+[\w-]+")


class Redactor:
    """Sostituisce persone e organizzazioni con etichette coerenti ("Persona A", "Azienda B"): la stessa entita' ha
    la stessa etichetta in tutto il brief, cosi' il testo resta leggibile. Indirizzi email e numeri di telefono
    vengono sempre coperti."""

    def __init__(self, redact_people: bool = True, redact_organizations: bool = True) -> None:
        self.redact_people = redact_people
        self.redact_organizations = redact_organizations
        self._labels: dict[str, str] = {}
        self._people = 0
        self._orgs = 0

    def _label(self, name: str, is_person: bool) -> str:
        key = name.strip().lower()
        if key not in self._labels:
            if is_person:
                self._labels[key] = f"Persona {chr(ord('A') + self._people % 26)}"
                self._people += 1
            else:
                self._labels[key] = f"Azienda {chr(ord('A') + self._orgs % 26)}"
                self._orgs += 1
        return self._labels[key]

    def apply(self, text: str, people: tuple[str, ...] = (), organizations: tuple[str, ...] = ()) -> str:
        result = _EMAIL.sub("[email]", text)
        result = re.sub(r"(?<!\w)\+?\d[\d ()/.-]{6,}\d(?!\w)", "[numero]", result)
        replacements = []
        if self.redact_people:
            replacements += [(n, self._label(n, True)) for n in people]
        if self.redact_organizations:
            replacements += [(n, self._label(n, False)) for n in organizations]
        for name, label in sorted(replacements, key=lambda r: len(r[0]), reverse=True):  # i nomi lunghi per primi
            result = re.sub(re.escape(name), label, result, flags=re.IGNORECASE)
        return result


# ---- adattatori per le fonti locali ------------------------------------------------------------------------------------------

class RemindersSource:
    """Promemoria non ancora scattati che scadono entro `horizon_hours`."""

    name = "promemoria"

    def __init__(self, reminder_manager, horizon_hours: float = 24.0) -> None:
        self._manager = reminder_manager
        self._horizon = horizon_hours

    def fetch(self, now: float) -> list[BriefItem]:
        limit = datetime.fromtimestamp(now) + timedelta(hours=self._horizon)
        items = []
        for reminder in self._manager.list_upcoming(limit=50):
            due = datetime.fromisoformat(reminder["due_at"])
            if due.tzinfo is not None:
                due = due.astimezone().replace(tzinfo=None)
            if due <= limit:
                items.append(BriefItem(
                    "deadlines", f"{due.strftime('%H:%M')} - {reminder['text']}", self.name, now, max_age_s=300.0,
                    priority=100 - int((due - datetime.fromtimestamp(now)).total_seconds() // 3600),
                ))
        return items


class TodosSource:
    """Attivita' ancora aperte; quelle vecchie di piu' giorni salgono di priorita' (le dimenticate)."""

    name = "todo"

    def __init__(self, todo_manager, stale_days: float = 3.0) -> None:
        self._manager = todo_manager
        self._stale_days = stale_days

    def fetch(self, now: float) -> list[BriefItem]:
        stale_ids = {t["id"] for t in self._manager.list_stale_pending(self._stale_days)}
        return [
            BriefItem("open_tasks", todo["text"] + (" (aperta da giorni)" if todo["id"] in stale_ids else ""),
                      self.name, now, max_age_s=600.0, priority=50 if todo["id"] in stale_ids else 10)
            for todo in self._manager.list_pending(limit=30)
        ]


# ---- costruzione ---------------------------------------------------------------------------------------------------------------

def _age_text(age_s: float) -> str:
    if age_s < 90:
        return "adesso"
    if age_s < 5400:
        return f"{round(age_s / 60)} min fa"
    if age_s < 172800:
        return f"{round(age_s / 3600)} h fa"
    return f"{round(age_s / 86400)} giorni fa"


@dataclass
class BriefBuilder:
    sources: list[BriefSource]
    clock: Callable[[], float] = time.time
    speaker_shared: bool = False
    redactor_factory: Callable[[], Redactor] = Redactor

    def build(self, format: str = "short", redact: bool = False) -> Brief:
        if format not in ("short", "detailed", "silent"):
            raise ValueError("format deve essere short, detailed o silent")
        now = self.clock()
        redactor = self.redactor_factory() if redact else None
        collected: list[BriefItem] = []
        unavailable: list[tuple[str, str]] = []
        rejected: list[tuple[str, str]] = []
        for source in self.sources:
            try:
                fetched = source.fetch(now)
            except Exception as exc:  # una fonte guasta non deve far sparire le altre ne' far inventare i suoi dati
                unavailable.append((getattr(source, "name", type(source).__name__), f"{type(exc).__name__}: {exc}"))
                continue
            for item in fetched:
                problem = self._validate(item, now)
                if problem:
                    rejected.append((item.text, problem))
                else:
                    collected.append(item)

        sections: dict[str, list[RenderedItem]] = {}
        omitted_stale = 0
        for section in SECTION_ORDER:
            rendered: list[RenderedItem] = []
            for item in sorted((i for i in collected if i.section == section or (section == "other" and i.section not in SECTION_ORDER)),
                               key=lambda i: (-i.priority, i.text)):
                age = now - item.fetched_at
                stale = age > item.max_age_s
                if stale and format == "short":
                    omitted_stale += 1
                    continue
                text = redactor.apply(item.text, item.people, item.organizations) if redactor else item.text
                rendered.append(RenderedItem(item, text, age, stale))
            if format == "short":
                rendered = rendered[:SHORT_LIMIT_PER_SECTION]
            if rendered:
                sections[section] = rendered
        text = self._render(sections, format, unavailable, omitted_stale, rejected)
        spoken = self._spoken(sections, text, format)
        return Brief(format, now, sections, unavailable, rejected, omitted_stale, text, spoken)

    @staticmethod
    def _validate(item: BriefItem, now: float) -> str | None:
        if not item.source or not item.source.strip():
            return "manca la fonte"
        if not item.text or not item.text.strip():
            return "testo vuoto"
        if item.fetched_at > now + 5:
            return "letto nel futuro: la provenienza non e' credibile"
        if item.sensitivity not in SENSITIVITY:
            return f"sensibilita' sconosciuta: {item.sensitivity!r}"
        return None

    def _render(self, sections, format, unavailable, omitted_stale, rejected) -> str:
        if not sections and not unavailable:
            return "Non ho dati da nessuna fonte per questa mattina."
        lines = []
        for section, items in sections.items():
            lines.append(f"{SECTION_LABELS.get(section, section)}:")
            for rendered in items:
                line = f"- {rendered.text}"
                if format == "detailed":
                    line += f" [{rendered.item.source}, {_age_text(rendered.age_s)}{', DATO VECCHIO' if rendered.stale else ''}]"
                lines.append(line)
        if omitted_stale:
            lines.append(f"({omitted_stale} dati non aggiornati non sono mostrati)")
        for name, reason in unavailable:
            lines.append(f"La fonte '{name}' non ha risposto ({reason}): non ho dati da quella parte.")
        if rejected and format == "detailed":
            lines.append(f"{len(rejected)} elementi scartati perche' senza una provenienza valida.")
        if not sections:
            lines.insert(0, "Non ho dati aggiornati da nessuna fonte.")
        return "\n".join(lines)

    def _spoken(self, sections, text: str, format: str) -> str | None:
        """Cio' che si puo' dire a voce. `silent`: niente. Su uno speaker condiviso solo elementi dichiarati pubblici."""
        if format == "silent":
            return None
        if not self.speaker_shared:
            return text
        public = [r.text for items in sections.values() for r in items if r.item.sensitivity == "public"]
        hidden = sum(1 for items in sections.values() for r in items if r.item.sensitivity != "public")
        parts = public[:]
        if hidden:
            parts.append(f"Ci sono {hidden} elementi privati: li trovi sullo schermo.")
        return " ".join(parts) if parts else None
