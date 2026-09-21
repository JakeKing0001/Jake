"""Parlato a pezzi, interrompibile (F2.5.2, F2.5.3, F2.5.4).

`ChunkedSpeaker` riceve il testo di una risposta man mano che arriva (i "delta" di un LLM in
streaming, oppure il testo intero in un colpo solo) e comincia a parlare appena la PRIMA frase e'
completa, senza aspettare la fine. Ogni unita' e' una chiamata separata a `provider.speak`, quindi
`cancel()` puo' fermare quella in corso E scartare tutte le successive in coda: dopo un barge-in
nessuna unita' gia' accodata viene pronunciata.

Cosa non fa, di proposito: non sceglie il motore (lo riceve), non decide *quando* interrompersi
(lo decide chi rileva la voce dell'utente, F2.4) e non legge codice ne' Markdown (lo toglie
`speech_text.clean_for_speech`, una sola volta per blocco anche se il blocco arriva a pezzi)."""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from core.logger import get_logger
from core.voice.speech_text import CODE_OMITTED, STYLES, SpeechStyle, clean_for_speech, split_prosodic

_BOUNDARY_CHARS = ".!?;\n"


class ChunkedSpeaker:
    def __init__(
        self,
        provider,
        style: SpeechStyle = STYLES["normal"],
        on_unit_start: Callable[[str], None] | None = None,
        on_unit_end: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_unit_chars: int = 220,
    ) -> None:
        self.provider = provider
        self.style = style
        self.on_unit_start = on_unit_start
        self.on_unit_end = on_unit_end
        self._clock = clock
        self._max_unit_chars = max_unit_chars
        self._logger = get_logger()
        self._cond = threading.Condition()
        self._reset_state()

    def _reset_state(self) -> None:
        self._raw = ""
        self._queue: deque[str] = deque()
        self._input_done = False
        self._cancelled = False
        self._code_announced = False
        self._units_enqueued = 0
        self._discarded_by_style = False
        self._worker: threading.Thread | None = None
        self._started_at: float | None = None
        self._first_speech_at: float | None = None
        self.units_spoken: list[str] = []
        self.units_dropped = 0
        self._idle = threading.Event()
        self._idle.set()

    # ---- ingresso ----------------------------------------------------------------------

    def feed(self, delta: str) -> None:
        """Aggiunge testo. Ignorato dopo cancel() o finish()."""
        if not delta:
            return
        with self._cond:
            if self._cancelled or self._input_done:
                return
            if self._started_at is None:
                self._started_at = self._clock()
            self._raw += delta
            self._enqueue(self._take_safe_prefix())
            self._ensure_worker()

    def finish(self) -> None:
        """Il testo e' finito: pronuncia anche l'ultima frase senza punteggiatura finale."""
        with self._cond:
            if self._cancelled or self._input_done:
                return
            self._input_done = True
            self._enqueue(self._raw)
            self._raw = ""
            if self._discarded_by_style and self.style.trailing_note:
                self._queue.append(self.style.trailing_note)
            self._ensure_worker()
            self._cond.notify_all()

    def speak(self, text: str) -> None:
        """Comodita' per un testo gia' completo."""
        self.feed(text)
        self.finish()

    def _take_safe_prefix(self) -> str:
        """Porzione di `_raw` che si puo' gia' pronunciare: fino all'ultimo confine di frase che non
        cada dentro un blocco di codice ancora aperto. Il resto resta nel buffer."""
        raw = self._raw
        cut = -1
        for index, char in enumerate(raw):
            if char in _BOUNDARY_CHARS and (index + 1 == len(raw) or raw[index + 1].isspace()):
                cut = index + 1
        if cut <= 0:
            return ""
        prefix = raw[:cut]
        if prefix.count("```") % 2 == 1:
            fence = prefix.rfind("```")
            cut = max(0, fence)
            prefix = raw[:cut]
        if not prefix.strip():
            return ""
        self._raw = raw[len(prefix):]
        return prefix

    def _enqueue(self, raw_text: str) -> None:
        if not raw_text.strip():
            return
        cleaned = clean_for_speech(raw_text)
        if CODE_OMITTED in cleaned:
            if self._code_announced:
                cleaned = cleaned.replace(CODE_OMITTED, "").strip()
            self._code_announced = True
        for unit in split_prosodic(cleaned, max_chars=self._max_unit_chars):
            if self.style.max_units is not None and self._units_enqueued >= self.style.max_units:
                self._discarded_by_style = True
                continue
            self._units_enqueued += 1
            self._queue.append(unit)
        if self._queue:
            self._idle.clear()

    # ---- riproduzione ------------------------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self._cond.notify_all()
            return
        if not self._queue:
            return
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._input_done and not self._cancelled:
                    self._cond.wait(timeout=0.5)
                if self._cancelled or not self._queue:
                    break
                unit = self._queue.popleft()
            self._speak_unit(unit)
        with self._cond:
            self._idle.set()

    def _speak_unit(self, unit: str) -> None:
        if self._first_speech_at is None:
            self._first_speech_at = self._clock()
        try:
            if self.on_unit_start is not None:
                self.on_unit_start(unit)
            self.provider.speak(unit)
            if not self._cancelled:
                self.units_spoken.append(unit)
        except Exception:
            self._logger.exception("Errore nella sintesi vocale di un'unita'")
        finally:
            if self.on_unit_end is not None:
                try:
                    self.on_unit_end(unit)
                except Exception:
                    self._logger.exception("Errore nel callback di fine unita'")

    # ---- interruzione ------------------------------------------------------------------

    def cancel(self) -> int:
        """Ferma subito l'unita' in corso e scarta quelle accodate e il testo non ancora elaborato
        (F2.5.3). Ritorna quante unita' accodate sono state scartate. Sicuro da chiamare piu' volte
        e dal thread del rilevatore di voce: non attende il thread di riproduzione."""
        with self._cond:
            if self._cancelled:
                return 0
            self._cancelled = True
            dropped = len(self._queue)
            self._queue.clear()
            self._raw = ""
            self.units_dropped += dropped
            self._cond.notify_all()
        try:
            self.provider.stop()
        except Exception:
            self._logger.exception("Errore fermando la sintesi vocale")
        worker = self._worker
        if worker is None or not worker.is_alive():
            self._idle.set()
        return dropped

    def wait(self, timeout: float | None = None) -> bool:
        """Attende la fine (o l'annullamento). True se e' finito entro il timeout."""
        if self._worker is None and not self._queue:
            return True
        return self._idle.wait(timeout)

    def reset(self) -> None:
        """Nuova risposta. Se c'e' ancora un thread vivo lo annulla prima."""
        if self._worker is not None and self._worker.is_alive():
            self.cancel()
            self._worker.join(timeout=2)
        with self._cond:
            self._reset_state()

    # ---- misure ------------------------------------------------------------------------

    @property
    def first_speech_latency_s(self) -> float | None:
        """Secondi dal primo testo ricevuto all'inizio della prima unita' (F2.5.2: < 2 s)."""
        if self._started_at is None or self._first_speech_at is None:
            return None
        return self._first_speech_at - self._started_at

    @property
    def cancelled(self) -> bool:
        return self._cancelled
