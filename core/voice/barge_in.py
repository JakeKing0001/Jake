"""Barge-in: l'utente interrompe Jake mentre parla (F2.4.3, F2.4.4, F2.4.5).

Tre pezzi separati, ciascuno testabile da solo:

- `BargeInDetector` decide, frame per frame, se e' la VOCE DELL'UTENTE che sta coprendo quella di
  Jake. Guarda il residuo dopo la cancellazione d'eco (`audio_frontend`), non il microfono grezzo:
  sul grezzo la voce di Jake stessa sembrerebbe sempre un'interruzione.
- `BargeInController` reagisce: ferma il parlato e scarta le unita' accodate (`ChunkedSpeaker.
  cancel`), conserva i primi istanti di parlato dell'utente (pre-roll, altrimenti "Jake, no" perderebbe
  il "Jake") e apre un NUOVO turno correlato a quello interrotto, ricordando cosa Jake aveva gia' detto.
- `classify_interruption` capisce cosa l'utente ha detto: fermati, correzione o nuova richiesta.

Nessun audio viene scritto su disco: il pre-roll vive in RAM e si svuota quando lo si consuma."""
from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np


class PreRollBuffer:
    """Gli ultimi `max_ms` di audio, sempre disponibili: quando scatta il barge-in l'utente sta
    parlando da qualche istante e quell'inizio serve al riconoscimento vocale."""

    def __init__(self, max_ms: int = 450, frame_ms: int = 30) -> None:
        self._frames: deque[np.ndarray] = deque(maxlen=max(1, max_ms // frame_ms))

    def push(self, frame: np.ndarray) -> None:
        self._frames.append(np.asarray(frame).reshape(-1).copy())

    def drain(self) -> np.ndarray:
        """Restituisce l'audio conservato e SVUOTA il buffer (nessuna copia resta in memoria)."""
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(list(self._frames))
        self._frames.clear()
        return audio

    def __len__(self) -> int:
        return len(self._frames)


class BargeInDetector:
    """Un frame conta come "voce dell'utente" se il VAD lo dichiara parlato E il livello del residuo
    supera una frazione (`echo_ratio`) del livello del riferimento riprodotto: dopo la cancellazione
    resta sempre un po' di eco, e cio' che non supera quel margine e' eco, non persona. Scatta dopo
    `min_frames` frame utili, tollerando fino a `max_gap` frame non utili in mezzo (le pause fra
    parole). Non scatta mai se Jake non sta parlando: in quel caso e' un comando normale, non
    un'interruzione."""

    def __init__(self, min_frames: int = 8, max_gap: int = 2, echo_ratio: float = 0.35, min_level: float = 0.01) -> None:
        self.min_frames = min_frames
        self.max_gap = max_gap
        self.echo_ratio = echo_ratio
        self.min_level = min_level
        self._count = 0
        self._gap = 0

    def reset(self) -> None:
        self._count = 0
        self._gap = 0

    def update(self, is_speech: bool, residual_level: float, reference_level: float, speaking: bool) -> bool:
        """True nel frame in cui l'interruzione viene riconosciuta."""
        if not speaking:
            self.reset()
            return False
        threshold = max(self.min_level, self.echo_ratio * reference_level)
        if is_speech and residual_level >= threshold:
            self._count += 1
            self._gap = 0
            if self._count >= self.min_frames:
                self.reset()
                return True
            return False
        if self._count:
            self._gap += 1
            if self._gap > self.max_gap:
                self.reset()
        return False


# ---- turni correlati --------------------------------------------------------------------------

@dataclass
class Turn:
    turn_id: int
    started_at: float
    parent_id: int | None = None  # il turno che questo ha interrotto
    kind: str = "normal"  # "normal" | "interruption"
    interrupted_said: str = ""  # cosa Jake aveva gia' detto del turno interrotto
    interrupted_dropped: int = 0  # quante unita' non sono mai state pronunciate


@dataclass
class TurnLog:
    turns: list[Turn] = field(default_factory=list)

    def start(self, now: float, parent: Turn | None = None, kind: str = "normal") -> Turn:
        turn = Turn(len(self.turns) + 1, now, parent.turn_id if parent else None, kind)
        self.turns.append(turn)
        return turn

    @property
    def current(self) -> Turn | None:
        return self.turns[-1] if self.turns else None


class BargeInController:
    """Collega rilevatore, parlato, pre-roll e turni. `on_frame` va chiamata per ogni frame del
    microfono gia' ripulito; `speaker` e' un `ChunkedSpeaker` (o qualunque oggetto con `cancel()`,
    `units_spoken` e `wait()`)."""

    def __init__(
        self,
        speaker: Any,
        detector: BargeInDetector | None = None,
        preroll: PreRollBuffer | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_barge_in: Callable[[Turn], None] | None = None,
    ) -> None:
        self.speaker = speaker
        self.detector = detector or BargeInDetector()
        self.preroll = preroll or PreRollBuffer()
        self._clock = clock
        self.on_barge_in = on_barge_in
        self.turns = TurnLog()
        self.last_stop_latency_s: float | None = None
        self.barge_in_count = 0

    def begin_response(self) -> Turn:
        """Un nuovo turno di Jake comincia a parlare."""
        self.detector.reset()
        return self.turns.start(self._clock())

    def on_frame(self, frame: np.ndarray, is_speech: bool, residual_level: float, reference_level: float, speaking: bool) -> Turn | None:
        """Ritorna il nuovo turno (di interruzione) se in questo frame e' scattato un barge-in."""
        self.preroll.push(frame)
        if not self.detector.update(is_speech, residual_level, reference_level, speaking):
            return None
        started = self._clock()
        interrupted = self.turns.current
        self.speaker.cancel()  # ferma l'unita' in corso E scarta la coda (F2.5.3)
        self.last_stop_latency_s = self._clock() - started
        self.barge_in_count += 1
        turn = self.turns.start(self._clock(), parent=interrupted, kind="interruption")
        turn.interrupted_said = " ".join(getattr(self.speaker, "units_spoken", []))
        turn.interrupted_dropped = int(getattr(self.speaker, "units_dropped", 0))
        if self.on_barge_in is not None:
            self.on_barge_in(turn)
        return turn

    def take_preroll(self) -> np.ndarray:
        """L'audio dell'utente prima del riconoscimento: da anteporre a quello che segue."""
        return self.preroll.drain()


# ---- cosa ha detto l'utente -------------------------------------------------------------------------

@dataclass(frozen=True)
class Interruption:
    kind: str  # "stop" | "correction" | "continue" | "new_request"
    remainder: str  # il testo utile, senza parola di attivazione e senza i "no, ..." iniziali


_WAKE_PREFIX = re.compile(r"^\s*(?:(?:ehi|hey|ok|okay|ciao)\s+)?(?:jake|geek|jack)[\s,.!?]*", re.IGNORECASE)
_STOP = re.compile(
    r"^(?:basta(?: cos[iì])?|stop|fermati|ferma|zitto|zitta|silenzio|smettila|taci|"
    r"non parlare|non serve|lascia stare|lascia perdere|va bene cos[iì]|ok basta|no basta|"
    r"never ?mind|be quiet|shut up|that'?s enough)[\s,.!?]*$",
    re.IGNORECASE,
)
_HOLD = re.compile(r"^(?:no|aspetta|aspetta un attimo|un attimo|un momento|wait|hold on)[\s,.!?]*$", re.IGNORECASE)
_CONTINUE = re.compile(r"^(?:continua|vai avanti|prosegui|riprendi|vai pure|go on|continue|keep going)[\s,.!?]*$", re.IGNORECASE)
# Segnali di correzione, tolti uno alla volta dall'inizio ("no, scusa, intendevo X" -> X).
_CORRECTION_MARKER = re.compile(
    r"^(?:no[\s,.!?]+|aspetta[\s,.!?]+|scusa[\s,.!?]+|anzi[\s,.!?]+|cio[eè]'?[\s,.!?]+|"
    r"intendevo\s+|volevo dire\s+|ho detto\s+|non ho detto .+? ma\s+|I meant\s+|actually[\s,.!?]+|no,? I meant\s+)",
    re.IGNORECASE,
)


def classify_interruption(text: str) -> Interruption:
    """Distingue fermati / correzione / continua / nuova richiesta (F2.4.5). Una parola sola come
    "no" o "aspetta" e' un STOP (l'utente vuole solo che Jake taccia): trattarla come correzione
    lascerebbe Jake in attesa di un seguito che forse non arriva. Un "no, intendevo X" e' una
    correzione con remainder X. Tutto il resto e' una nuova richiesta."""
    cleaned = _WAKE_PREFIX.sub("", text or "", count=1).strip()
    if not cleaned:
        return Interruption("stop", "")
    if _STOP.match(cleaned) or _HOLD.match(cleaned):
        return Interruption("stop", "")
    if _CONTINUE.match(cleaned):
        return Interruption("continue", "")
    remainder, stripped = cleaned, False
    while True:
        marker = _CORRECTION_MARKER.match(remainder)
        if marker is None or not remainder[marker.end():].strip():
            break
        remainder, stripped = remainder[marker.end():].strip(), True
    if stripped:
        return Interruption("correction", remainder)
    return Interruption("new_request", cleaned)
