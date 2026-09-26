"""Partial in streaming fuori dal thread di ascolto (F2.2.1, F2.2.2, F2.2.4).

Il thread di ascolto (`VadListener.listen_for_utterances`) non deve MAI aspettare Whisper: se un frame
tarda a essere letto dalla coda il microfono perde audio. `LiveTranscriber` riceve i frame di una frase
in corso con `feed()` (che non blocca: prende un lock breve e appende a un buffer limitato) e li
trascrive su un thread proprio, ogni `interval_s`, con politica "l'ultimo vince": se una decodifica e'
ancora in corso quando ne servirebbe un'altra, la richiesta vecchia non si accoda, si salta.

Il riconoscimento e' quello di `StreamingTranscriber` (accordo locale su ipotesi consecutive, budget,
degrado): qui c'e' solo il thread, il lock verso il modello e la consegna degli eventi. Il testo FINALE
non nasce qui: lo produce, come prima, la trascrizione della frase intera; `LiveTranscriber` fornisce
l'`utterance_id` e la revisione successiva cosi' partial e final appartengono alla stessa frase.

Un solo modello Whisper non regge due decodifiche contemporanee: `model_lock` (condiviso con chi fa la
trascrizione finale) le serializza. Il costo e' che una finale puo' aspettare la fine di un partial: e'
per questo che chi costruisce la sessione abilita i partial solo dove una decodifica e' veloce (GPU)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from core.logger import get_logger
from core.voice.streaming_stt import KIND_FINAL, StreamingTranscriber, TranscriptEvent


class LiveTranscriber:
    def __init__(
        self,
        provider,
        on_event: Callable[[TranscriptEvent], None],
        model_lock: threading.Lock | None = None,
        sample_rate: int = 16000,
        interval_s: float = 0.6,
        partial_budget_s: float = 1.0,
        max_buffer_s: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_event = on_event
        self._model_lock = model_lock or threading.Lock()
        self._locked_provider = _LockedProvider(provider, self._model_lock)
        self._factory_args: dict[str, Any] = {
            "sample_rate": sample_rate, "partial_interval_s": interval_s, "max_buffer_s": max_buffer_s,
            "partial_budget_s": partial_budget_s, "clock": clock,
        }
        self._transcriber = self._new_transcriber()
        self._sample_rate = sample_rate
        self._interval_samples = int(interval_s * sample_rate)
        self._logger = get_logger()
        self._cond = threading.Condition()
        self._pending: list[np.ndarray] = []
        self._active = False
        self._closed = False
        self._decoding = False
        self.skipped_decodes = 0  # richieste saltate perche' una decodifica era ancora in corso
        self._worker = threading.Thread(target=self._run, daemon=True, name="live-transcriber")
        self._worker.start()

    def _new_transcriber(self) -> StreamingTranscriber:
        return StreamingTranscriber(self._locked_provider, **self._factory_args)

    # ---- ingresso (thread di ascolto) -------------------------------------------------------------------

    @property
    def utterance_id(self) -> str:
        return self._transcriber.utterance_id

    @property
    def degraded(self) -> bool:
        return self._transcriber.degraded

    def start_utterance(self) -> None:
        """Comincia una frase nuova (annulla quella eventualmente in corso). Non aspetta una decodifica in
        corso: il trascrittore precedente viene SOSTITUITO (il thread di decodifica ne tiene un riferimento
        locale e i suoi eventi, con un utterance_id vecchio, vengono scartati) invece di essere azzerato
        mentre un altro thread lo sta usando."""
        with self._cond:
            self._pending = []
            self._transcriber = self._new_transcriber()
            self._active = True

    def feed(self, frame: np.ndarray) -> None:
        """Un frame int16 o float32 della frase in corso. Non blocca su una decodifica."""
        chunk = np.asarray(frame).reshape(-1)
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32) / 32768.0
        with self._cond:
            if not self._active or self._closed:
                return
            self._pending.append(chunk)
            if sum(len(c) for c in self._pending) >= self._interval_samples:
                if self._decoding:
                    self.skipped_decodes += 1  # l'ultimo vince: i frame restano accodati, non si lancia un secondo lavoro
                self._cond.notify()

    def end_utterance(self) -> tuple[str, int]:
        """La frase e' finita: ferma i partial, svuota il buffer e ritorna (utterance_id, prossima revisione)
        da usare per l'evento finale. Idempotente."""
        with self._cond:
            self._active = False
            self._pending = []
            utterance_id = self._transcriber.utterance_id
            revision = self._transcriber.revision + 1
        return utterance_id, revision

    def final_event(self, text: str, confidence: float | None, utterance_id: str, revision: int) -> TranscriptEvent:
        return TranscriptEvent(utterance_id, revision, KIND_FINAL, text, text, confidence)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._active = False
            self._cond.notify_all()
        self._worker.join(timeout=2)

    # ---- thread di decodifica ---------------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._closed and not (self._active and self._pending):
                    self._cond.wait(timeout=0.5)
                if self._closed:
                    return
                if sum(len(c) for c in self._pending) < self._interval_samples:
                    self._cond.wait(timeout=0.1)
                    continue
                chunks, self._pending = self._pending, []
                self._decoding = True
                transcriber = self._transcriber
                utterance_id = transcriber.utterance_id
            try:
                # UN solo blocco con tutto l'audio arrivato durante la decodifica precedente: "l'ultimo
                # vince". Pushare i frame uno per uno farebbe partire una decodifica ogni 0,6 s di audio
                # ACCUMULATO, tutte su testo gia' vecchio, mentre l'utente continua a parlare.
                events = transcriber.push_audio(np.concatenate(chunks))
            except Exception:
                self._logger.exception("Errore nella decodifica parziale")
                events = []
            finally:
                with self._cond:
                    self._decoding = False
            with self._cond:
                still_same = self._active and utterance_id == self._transcriber.utterance_id
            if not still_same:
                continue  # la frase e' finita mentre si decodificava: un partial tardivo dopo il final sarebbe rumore
            for event in events:
                try:
                    self._on_event(event)
                except Exception:
                    self._logger.exception("Errore nel callback dei partial")


class SttModelLock:
    """Lock del modello Whisper con PRIORITA' alla trascrizione finale (gate hardware F2, 26/09/2026:
    la finale aspettava partial da 1-5 s sullo stesso lock, e il comando partiva in ritardo).

    La finale usa `with lock:` e aspetta al massimo il partial GIA' in corso (una decodifica non si
    interrompe a meta'); mentre una finale aspetta, nessun nuovo partial parte: `try_acquire_partial`
    non blocca mai e rinuncia se il modello e' occupato o se una finale e' in attesa."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self._finals_waiting = 0

    def __enter__(self) -> SttModelLock:
        with self._guard:
            self._finals_waiting += 1
        try:
            self._lock.acquire()
        finally:
            with self._guard:
                self._finals_waiting -= 1
        return self

    def __exit__(self, *exc) -> None:
        self._lock.release()

    def try_acquire_partial(self) -> bool:
        with self._guard:
            if self._finals_waiting:
                return False
            return self._lock.acquire(blocking=False)

    def release_partial(self) -> None:
        self._lock.release()


class _LockedProvider:
    """Serializza le chiamate al modello con un lock condiviso con la trascrizione finale."""

    def __init__(self, provider, lock) -> None:
        self._provider = provider
        self._lock = lock
        if hasattr(provider, "transcribe_detailed"):
            self.transcribe_detailed = self._detailed

    def transcribe_partial(self, audio, sample_rate):
        """Decodifica per un partial: (testo, confidenza), oppure None se il modello serve alla finale
        (il partial si salta, non si degrada). Usa la decodifica leggera del provider se c'e'."""
        if hasattr(self._lock, "try_acquire_partial"):
            if not self._lock.try_acquire_partial():
                return None
            release = self._lock.release_partial
        else:
            self._lock.acquire()
            release = self._lock.release
        try:
            fast = getattr(self._provider, "transcribe_partial", None)
            if fast is not None:
                return fast(audio, sample_rate)
            detailed = getattr(self._provider, "transcribe_detailed", None)
            if detailed is not None:
                return detailed(audio, sample_rate)
            return self._provider.transcribe(audio, sample_rate), None
        finally:
            release()

    def transcribe(self, audio, sample_rate):
        with self._lock:
            return self._provider.transcribe(audio, sample_rate)

    def _detailed(self, audio, sample_rate):
        with self._lock:
            return self._provider.transcribe_detailed(audio, sample_rate)
