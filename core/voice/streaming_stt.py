"""Trascrizione in streaming con partial revisionabili (F2.2.2, F2.2.3, F2.2.4, F2.2.6).

Whisper non e' un motore incrementale: trascrive un pezzo di audio alla volta. Lo streaming qui e'
quindi la tecnica di "accordo locale" (LocalAgreement-2, la stessa di whisper-streaming): a
intervalli regolari si ritrascrive l'audio accumulato e si considera *stabile* solo il prefisso di
parole su cui due ipotesi consecutive concordano. Il resto e' provvisorio e puo' cambiare.

Regole fissate qui, ciascuna con un test:

- un partial non e' mai un comando: solo l'evento `final` puo' arrivare al router
  (`TranscriptRouter`, F2.2.3);
- il testo stabile non arretra: una parola gia' confermata non sparisce dai partial successivi;
- l'audio vive in un buffer limitato in RAM; se la trascrizione non tiene il passo si scartano i
  campioni piu' VECCHI e si conta quanti (F2.2.4), mai la coda piu' recente;
- se una trascrizione parziale costa piu' del budget, i partial si spengono per il resto della
  frase e resta la sola trascrizione finale a frase completa (F2.2.6): meglio nessun partial che
  un partial in ritardo;
- alla finalizzazione il buffer viene svuotato (F2.2.5) e la frase produce UN solo `final`."""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.hud_protocol import EventType, HudEvent
from core.logger import get_logger

TRANSCRIPT_SCHEMA_VERSION = 1
KIND_PARTIAL = "partial"
KIND_FINAL = "final"


@dataclass(frozen=True)
class TranscriptEvent:
    utterance_id: str
    revision: int  # 1, 2, 3... per la stessa frase: un client scarta una revisione piu' vecchia
    kind: str  # KIND_PARTIAL | KIND_FINAL
    text: str
    stable_text: str  # prefisso su cui le ultime due ipotesi concordano (== text nel final)
    confidence: float | None = None

    def to_payload(self) -> dict[str, Any]:
        """Payload dell'evento HUD/companion (F2.2.7): versionato, solo tipi JSON."""
        return {
            "transcript_version": TRANSCRIPT_SCHEMA_VERSION,
            "utterance_id": self.utterance_id,
            "revision": self.revision,
            "kind": self.kind,
            "text": self.text,
            "stable_text": self.stable_text,
            "confidence": self.confidence,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TranscriptEvent:
        version = payload.get("transcript_version")
        if version != TRANSCRIPT_SCHEMA_VERSION:
            raise ValueError(f"transcript_version non supportata: {version!r} (attesa {TRANSCRIPT_SCHEMA_VERSION})")
        kind = payload.get("kind")
        if kind not in (KIND_PARTIAL, KIND_FINAL):
            raise ValueError(f"kind non valido: {kind!r}")
        for key in ("utterance_id", "text", "stable_text"):
            if not isinstance(payload.get(key), str):
                raise ValueError(f"campo '{key}' mancante o non testuale")
        revision = payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError(f"revision non valida: {revision!r}")
        confidence = payload.get("confidence")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))):
            raise ValueError(f"confidence non numerica: {confidence!r}")
        return cls(payload["utterance_id"], revision, kind, payload["text"], payload["stable_text"], confidence)


def to_hud_event(event: TranscriptEvent) -> HudEvent:
    """Evento del bus (F2.2.7) per HUD e companion; il payload e' quello versionato dell'evento."""
    return HudEvent(EventType.TRANSCRIPT, event.to_payload())


def _words(text: str) -> list[str]:
    return text.split()


class LocalAgreementStabilizer:
    """Tiene il prefisso stabile di una frase. `update(ipotesi)` ritorna il testo stabile
    corrente: le parole su cui l'ipotesi precedente e quella nuova concordano (confronto senza
    maiuscole e punteggiatura finale), senza mai accorciare cio' che e' gia' stato confermato."""

    def __init__(self) -> None:
        self._previous: list[str] = []
        self._committed: list[str] = []

    @staticmethod
    def _key(word: str) -> str:
        return word.lower().strip(".,;:!?\"'")

    def update(self, hypothesis: str) -> str:
        words = _words(hypothesis)
        agreed = 0
        for old, new in zip(self._previous, words, strict=False):
            if self._key(old) != self._key(new):
                break
            agreed += 1
        if agreed > len(self._committed):
            self._committed = words[:agreed]
        self._previous = words
        return " ".join(self._committed)

    @property
    def stable_text(self) -> str:
        return " ".join(self._committed)

    def reset(self) -> None:
        self._previous = []
        self._committed = []


class BoundedAudioBuffer:
    """Buffer audio in RAM con capienza massima (F2.2.4, F2.2.5). Oltre il limite scarta i campioni
    piu' vecchi e li conta in `dropped_samples`: chi guarda il risultato sa che la frase e'
    incompleta invece di credere a un audio intero."""

    def __init__(self, max_seconds: float, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self.max_samples = max(1, int(max_seconds * sample_rate))
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self.dropped_samples = 0

    def __len__(self) -> int:
        return self._samples

    def push(self, chunk: np.ndarray) -> None:
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return
        self._chunks.append(chunk)
        self._samples += chunk.size
        overflow = self._samples - self.max_samples
        while overflow > 0 and self._chunks:
            head = self._chunks[0]
            if head.size <= overflow:
                self._chunks.pop(0)
                self._samples -= head.size
                self.dropped_samples += head.size
                overflow -= head.size
            else:
                self._chunks[0] = head[overflow:]
                self._samples -= overflow
                self.dropped_samples += overflow
                overflow = 0

    def snapshot(self) -> np.ndarray:
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks)

    def clear(self) -> None:
        self._chunks = []
        self._samples = 0
        self.dropped_samples = 0


class StreamingTranscriber:
    """Una frase alla volta: `push_audio()` mentre arriva, `finish()` alla fine.

    `provider.transcribe(audio, sample_rate)` e' l'interfaccia di SttProvider; se il provider ha
    anche `transcribe_detailed(audio, sample_rate) -> (testo, confidenza|None)` viene preferita,
    per riportare la confidenza vera invece di inventarla."""

    def __init__(
        self,
        provider: Any,
        sample_rate: int = 16000,
        partial_interval_s: float = 0.6,
        max_buffer_s: float = 20.0,
        partial_budget_s: float | None = 1.0,
        streaming: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.sample_rate = sample_rate
        self.partial_interval_samples = int(partial_interval_s * sample_rate)
        self.partial_budget_s = partial_budget_s
        self.streaming = streaming
        self.degraded = not streaming
        self._clock = clock
        self._buffer = BoundedAudioBuffer(max_buffer_s, sample_rate)
        self._stabilizer = LocalAgreementStabilizer()
        self._logger = get_logger()
        self._new_utterance()

    def _new_utterance(self) -> None:
        self.utterance_id = uuid.uuid4().hex[:12]
        self._revision = 0
        self._samples_at_last_decode = 0
        self._last_partial_text: str | None = None
        self._finished = False

    @property
    def dropped_samples(self) -> int:
        return self._buffer.dropped_samples

    @property
    def buffered_samples(self) -> int:
        return len(self._buffer)

    def _decode(self, audio: np.ndarray) -> tuple[str, float | None]:
        detailed = getattr(self.provider, "transcribe_detailed", None)
        if detailed is not None:
            text, confidence = detailed(audio, self.sample_rate)
            return str(text).strip(), confidence
        return str(self.provider.transcribe(audio, self.sample_rate)).strip(), None

    def push_audio(self, chunk: np.ndarray) -> list[TranscriptEvent]:
        """Aggiunge audio; ritorna 0 o 1 partial (mai un final: quello lo produce solo finish())."""
        if self._finished:
            raise RuntimeError("frase gia' finalizzata: chiama reset() prima di un nuovo audio")
        self._buffer.push(chunk)
        if self.degraded or len(self._buffer) - self._samples_at_last_decode < self.partial_interval_samples:
            return []
        self._samples_at_last_decode = len(self._buffer)
        started = self._clock()
        try:
            text, confidence = self._decode(self._buffer.snapshot())
        except Exception:
            self._logger.exception("Errore nella trascrizione parziale")
            self.degraded = True
            return []
        elapsed = self._clock() - started
        if self.partial_budget_s is not None and elapsed > self.partial_budget_s:
            # In ritardo: questo risultato e' gia' vecchio e il prossimo lo sarebbe ancora di piu'.
            self.degraded = True
            self._logger.warning("Partial troppo lento (%.2f s > %.2f s): streaming disattivato per questa frase", elapsed, self.partial_budget_s)
            return []
        stable = self._stabilizer.update(text)
        if not text or text == self._last_partial_text:
            return []
        self._last_partial_text = text
        self._revision += 1
        return [TranscriptEvent(self.utterance_id, self._revision, KIND_PARTIAL, text, stable, confidence)]

    def finish(self) -> TranscriptEvent | None:
        """Trascrive l'audio completo e chiude la frase: UN solo final, poi buffer svuotato. None
        se gia' finalizzata (mai un secondo final per la stessa frase) o se non c'e' audio."""
        if self._finished:
            return None
        self._finished = True
        audio = self._buffer.snapshot()
        try:
            if audio.size == 0:
                return None
            try:
                text, confidence = self._decode(audio)
            except Exception:
                self._logger.exception("Errore nella trascrizione finale")
                return None
            self._revision += 1
            return TranscriptEvent(self.utterance_id, self._revision, KIND_FINAL, text, text, confidence)
        finally:
            self._buffer.clear()  # F2.2.5: nessun audio resta in RAM dopo la finalizzazione

    def reset(self) -> None:
        """Pronto per una nuova frase (annulla anche quella in corso, buffer compreso)."""
        self._buffer.clear()
        self._stabilizer.reset()
        self._new_utterance()
        self.degraded = not self.streaming


class TranscriptRouter:
    """Diga tra la trascrizione e il resto di Jake (F2.2.3): i partial vanno solo a `on_partial`
    (HUD, sottotitoli); solo un `final` non vuoto e non ripetuto arriva a `on_final`, l'unico
    punto da cui parte un comando."""

    def __init__(self, on_final: Callable[[TranscriptEvent], None], on_partial: Callable[[TranscriptEvent], None] | None = None) -> None:
        self.on_final = on_final
        self.on_partial = on_partial
        self._last_final_id: str | None = None
        self._last_partial_revision: dict[str, int] = {}

    def handle(self, event: TranscriptEvent) -> bool:
        """True se l'evento e' stato consegnato a un callback."""
        if event.kind == KIND_PARTIAL:
            if self.on_partial is None:
                return False
            # una revisione piu' vecchia di una gia' vista (evento arrivato fuori ordine) e' scartata
            if event.revision <= self._last_partial_revision.get(event.utterance_id, 0):
                return False
            self._last_partial_revision = {event.utterance_id: event.revision}
            self.on_partial(event)
            return True
        if event.kind != KIND_FINAL or not event.text.strip() or event.utterance_id == self._last_final_id:
            return False
        self._last_final_id = event.utterance_id
        self._last_partial_revision = {}
        self.on_final(event)
        return True
