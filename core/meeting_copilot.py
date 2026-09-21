"""Copilota per le riunioni: preparazione, consenso, follow-up (F6.6.4, F6.6.5, F6.6.6, F6.6.7).

Tre regole, ciascuna con un test, perche' sono quelle che fanno la differenza tra un aiuto e una violazione:

1. **Registrare o trascrivere solo con consenso evidente E indicatore attivo** (F6.6.5). Il consenso e' un
   atto scritto (`ConsentRecord`): chi lo da', che i partecipanti sono stati informati, quando. Senza, non
   si comincia. E se l'indicatore del microfono (`core/voice/listening_state.py::MicIndicator`) smette di
   dire "aperto" mentre si trascrive, la trascrizione SI FERMA da sola: registrare a indicatore spento e'
   esattamente il caso da impedire. Il testo trascritto vive in RAM e ha una scadenza; le note tenute sono
   una scelta esplicita.
2. **Nessun dato inventato nella preparazione** (F6.6.4): documenti e decisioni correlate arrivano da fonti
   che li dichiarano; se non ce ne sono, la preparazione dice "nessun documento correlato trovato" invece di
   suggerirne.
3. **Un follow-up e' una PROPOSTA, mai un invio** (F6.6.6): le azioni si estraggono solo da marcatori
   espliciti nelle note ("AZIONE:", "TODO:", "DECISIONE:"), e inviarle (a persone esterne) richiede una
   conferma esplicita dell'utente per quello specifico messaggio - non esiste un percorso di invio senza."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from core.daily_brief import Redactor


class MeetingState(str, Enum):
    PLANNED = "planned"
    CONSENT_PENDING = "consent_pending"
    TRANSCRIBING = "transcribing"
    PAUSED = "paused"
    ENDED = "ended"


class ConsentError(Exception):
    """Manca il consenso, o l'indicatore non e' attivo: non si trascrive."""


class ApprovalRequiredError(Exception):
    """Un invio senza la conferma esplicita dell'utente."""


@dataclass(frozen=True)
class ConsentRecord:
    granted_by: str  # chi ha dato il consenso (il proprietario)
    participants_informed: bool  # i partecipanti sanno che si trascrive
    statement: str  # a parole proprie
    at: float

    def is_evident(self) -> bool:
        return bool(self.granted_by.strip()) and self.participants_informed is True and len(self.statement.strip()) >= 10


@dataclass(frozen=True)
class Source:
    name: str
    fetched_at: float


@dataclass(frozen=True)
class RelatedItem:
    kind: str  # "document" | "decision"
    title: str
    source: Source


@dataclass(frozen=True)
class Preparation:
    title: str
    participants: tuple[str, ...]
    documents: tuple[RelatedItem, ...]
    decisions: tuple[RelatedItem, ...]
    notes: tuple[str, ...]

    def text(self) -> str:
        lines = [f"Preparazione: {self.title}", f"Partecipanti: {', '.join(self.participants) or 'non indicati'}"]
        lines.append("Documenti correlati: " + ("; ".join(f"{d.title} [{d.source.name}]" for d in self.documents) if self.documents else "nessun documento correlato trovato."))
        lines.append("Decisioni correlate: " + ("; ".join(f"{d.title} [{d.source.name}]" for d in self.decisions) if self.decisions else "nessuna decisione correlata trovata."))
        lines.extend(self.notes)
        return "\n".join(lines)


@dataclass
class FollowUp:
    text: str
    kind: str  # "action" | "decision"
    owner: str | None = None
    status: str = "proposed"  # proposed | approved | sent | rejected


_MARKER = re.compile(r"^\s*(AZIONE|TODO|DECISIONE|ACTION)\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_OWNER = re.compile(r"\(\s*(?:a|per|di|@)\s*([^)]+?)\s*\)\s*$|@(\w+)\s*$", re.IGNORECASE)


class MeetingCopilot:
    def __init__(
        self,
        title: str,
        participants: list[str],
        indicator_active: Callable[[], bool],
        clock: Callable[[], float] = time.time,
        transcript_ttl_s: float = 7 * 86400,
    ) -> None:
        self.title = title
        self.participants = tuple(participants)
        self._indicator_active = indicator_active
        self._clock = clock
        self.transcript_ttl_s = transcript_ttl_s
        self.state = MeetingState.PLANNED
        self.consent: ConsentRecord | None = None
        self._segments: list[tuple[float, str]] = []
        self.started_at: float | None = None
        self.paused_reason: str | None = None
        self.retained_notes: list[str] = []

    # ---- preparazione (F6.6.4) --------------------------------------------------------------------------------

    def prepare(self, document_sources: list, decision_sources: list) -> Preparation:
        """Documenti e decisioni correlati, ciascuno con la propria fonte. Una fonte che fallisce non inventa nulla:
        si aggiunge una nota che dice quale non ha risposto."""
        documents: list[RelatedItem] = []
        decisions: list[RelatedItem] = []
        notes: list[str] = []
        for kind, sources, target in (("document", document_sources, documents), ("decision", decision_sources, decisions)):
            for source in sources:
                try:
                    for title in source.related(self.title, self.participants):
                        target.append(RelatedItem(kind, title, Source(source.name, self._clock())))
                except Exception as exc:
                    notes.append(f"La fonte '{getattr(source, 'name', '?')}' non ha risposto ({type(exc).__name__}).")
        return Preparation(self.title, self.participants, tuple(documents), tuple(decisions), tuple(notes))

    # ---- consenso e trascrizione (F6.6.5) ------------------------------------------------------------------------

    def record_consent(self, granted_by: str, participants_informed: bool, statement: str) -> ConsentRecord:
        record = ConsentRecord(granted_by, participants_informed, statement, self._clock())
        self.consent = record
        self.state = MeetingState.CONSENT_PENDING if not record.is_evident() else self.state
        return record

    def start_transcription(self) -> None:
        if self.consent is None or not self.consent.is_evident():
            raise ConsentError("serve un consenso evidente: chi lo da', che i partecipanti sono informati, con una dichiarazione")
        if not self._indicator_active():
            raise ConsentError("l'indicatore del microfono non e' attivo: non si trascrive")
        self.state = MeetingState.TRANSCRIBING
        self.started_at = self._clock()
        self.paused_reason = None

    def add_segment(self, text: str) -> bool:
        """Aggiunge una frase trascritta. Se l'indicatore si e' spento la trascrizione si FERMA e la frase e' scartata."""
        if self.state != MeetingState.TRANSCRIBING:
            return False
        if not self._indicator_active():
            self.state = MeetingState.PAUSED
            self.paused_reason = "indicatore del microfono spento: trascrizione sospesa"
            return False
        if text.strip():
            self._segments.append((self._clock(), text.strip()))
        return True

    def resume(self) -> None:
        if self.state != MeetingState.PAUSED:
            raise ConsentError("la trascrizione non e' in pausa")
        if not self._indicator_active():
            raise ConsentError("l'indicatore del microfono non e' ancora attivo")
        self.state = MeetingState.TRANSCRIBING
        self.paused_reason = None

    def transcript(self) -> list[str]:
        """Le frasi ancora valide (quelle piu' vecchie della scadenza non ci sono piu')."""
        cutoff = self._clock() - self.transcript_ttl_s
        self._segments = [(t, s) for t, s in self._segments if t >= cutoff]
        return [s for _, s in self._segments]

    def end(self, keep_notes: list[str] | None = None, keep_transcript: bool = False) -> None:
        """Fine riunione. Le note si tengono solo se richiesto; la trascrizione si butta a meno che l'utente dica di no."""
        self.state = MeetingState.ENDED
        self.retained_notes = list(keep_notes or [])
        if not keep_transcript:
            self._segments = []

    # ---- follow-up (F6.6.6, F6.6.7) -----------------------------------------------------------------------------------------

    @staticmethod
    def extract_follow_ups(notes: str) -> list[FollowUp]:
        """Solo da marcatori ESPLICITI (AZIONE:/TODO:/DECISIONE:): nessuna deduzione da frasi qualunque."""
        follow_ups = []
        for marker, body in _MARKER.findall(notes):
            owner = None
            match = _OWNER.search(body)
            if match:
                owner = (match.group(1) or match.group(2)).strip()
                body = body[:match.start()].rstrip()
            follow_ups.append(FollowUp(body, "decision" if marker.upper() == "DECISIONE" else "action", owner))
        return follow_ups

    @staticmethod
    def redact_notes(notes: str, participants: list[str], organizations: list[str] | None = None) -> str:
        """Note per condividere fuori dalla riunione: partecipanti e organizzazioni coperti in modo coerente."""
        return Redactor().apply(notes, tuple(participants), tuple(organizations or []))


@dataclass
class PendingSend:
    """Un follow-up pronto per essere inviato a QUALCUNO. Non parte da solo: serve `approve()` per QUESTO invio."""

    follow_up: FollowUp
    recipient: str
    channel: str
    _sender: Callable[[str, str, str], None]
    approved_by: str | None = None
    sent: bool = field(default=False)

    def approve(self, user: str) -> None:
        if not user.strip():
            raise ApprovalRequiredError("serve l'utente che approva")
        self.approved_by = user
        self.follow_up.status = "approved"

    def send(self) -> None:
        if self.approved_by is None:
            raise ApprovalRequiredError("invio non approvato dall'utente: un follow-up e' una proposta, non un invio")
        if self.sent:
            raise ApprovalRequiredError("gia' inviato: nessun secondo invio")
        self._sender(self.channel, self.recipient, self.follow_up.text)
        self.sent = True
        self.follow_up.status = "sent"

    def reject(self) -> None:
        self.follow_up.status = "rejected"
        self.approved_by = None
