"""Metriche della sessione vocale dal vivo per il gate hardware di F2 (docs/f2-hardware-validation.md).

Jake misura da se' cio' che puo' misurare senza strumenti esterni, e solo quello:

- barge-in: interruzioni riconosciute e latenza voce-utente -> TTS fermo. La latenza e' la finestra
  di riconoscimento (frame di voce contati dal rilevatore) piu' il tempo di stop del provider: e' un
  LIMITE INFERIORE, perche' non include la latenza d'ingresso della scheda audio. Per questo il
  report la dichiara `method: internal_lower_bound` e il documento suggerisce una verifica a
  campione con una registrazione loopback;
- prima emissione TTS: dalla fine della frase dell'utente al primo campione audio mandato
  all'uscita (e, separatamente, dalla risposta pronta al primo campione);
- partial: dall'inizio della frase al primo partial pubblicato e i finali duplicati (devono restare 0);
- wake: attivazioni accettate con l'ora, eco ignorate, comandi eseguiti.

Mai audio, mai testo trascritto: solo numeri e conteggi. I conteggi che richiedono un giudizio umano
(tentativi di interruzione fatti, falsi stop, comandi prodotti dall'eco, wake mancati) li inserisce
chi fa la prova (vedi `benchmarks/f2_hardware_session.py`)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class SessionMetrics:
    def __init__(self, clock: Callable[[], float] = time.monotonic, wall_clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self.started_at = wall_clock()
        self.barge_in_latency_ms: list[float] = []
        self.first_emission_ms: list[float] = []
        self.response_to_audio_ms: list[float] = []
        self.first_partial_ms: list[float] = []
        self.wake_times: list[float] = []
        self.echo_ignored = 0
        self.commands = 0
        self.duplicate_finals = 0
        self._utterance_started: float | None = None
        self._partial_seen = False
        self._last_final_id: str | None = None
        self._utterance_end: float | None = None
        self._response_ready: float | None = None
        self._awaiting_audio = False

    def now(self) -> float:
        return self._clock()

    # ---- barge-in ---------------------------------------------------------------------------

    def barge_in(self, detection_window_s: float, stop_latency_s: float) -> None:
        with self._lock:
            self.barge_in_latency_ms.append(round((detection_window_s + stop_latency_s) * 1000, 1))

    # ---- partial / final --------------------------------------------------------------------

    def utterance_frame(self) -> None:
        with self._lock:
            if self._utterance_started is None:
                self._utterance_started = self._clock()
                self._partial_seen = False

    def partial(self) -> None:
        with self._lock:
            if self._utterance_started is not None and not self._partial_seen:
                self._partial_seen = True
                self.first_partial_ms.append(round((self._clock() - self._utterance_started) * 1000, 1))

    def final(self, utterance_id: str) -> None:
        with self._lock:
            if utterance_id == self._last_final_id:
                self.duplicate_finals += 1
            self._last_final_id = utterance_id
            self._utterance_started = None

    # ---- turno e prima emissione --------------------------------------------------------------

    def utterance_end(self, hangover_s: float = 0.0) -> None:
        """La frase e' stata consegnata dal VAD, che la chiude solo dopo `hangover_s` di silenzio:
        la voce dell'utente era finita prima, ed e' da li' che l'attesa si percepisce."""
        with self._lock:
            self._utterance_end = self._clock() - max(0.0, hangover_s)

    def command(self) -> None:
        with self._lock:
            self.commands += 1
            self._awaiting_audio = True
            self._response_ready = None

    def response_ready(self) -> None:
        with self._lock:
            if self._awaiting_audio:
                self._response_ready = self._clock()

    def audio_started(self) -> None:
        """Primo campione della risposta verso l'uscita (reference sink del provider)."""
        with self._lock:
            if not self._awaiting_audio:
                return
            self._awaiting_audio = False
            now = self._clock()
            if self._utterance_end is not None:
                self.first_emission_ms.append(round((now - self._utterance_end) * 1000, 1))
            if self._response_ready is not None:
                self.response_to_audio_ms.append(round((now - self._response_ready) * 1000, 1))

    # ---- wake ---------------------------------------------------------------------------------

    def wake(self) -> None:
        with self._lock:
            self.wake_times.append(self._wall_clock())

    def echo(self) -> None:
        with self._lock:
            self.echo_ignored += 1

    # ---- report -------------------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "hours": round((self._wall_clock() - self.started_at) / 3600, 3),
                "barge_in": {"detected": len(self.barge_in_latency_ms), "latency_ms": list(self.barge_in_latency_ms),
                             "method": "internal_lower_bound"},
                "streaming": {"partial_latency_ms": list(self.first_partial_ms), "duplicate_finals": self.duplicate_finals,
                              "definition": "inizio frase -> primo partial pubblicato"},
                "tts": {"first_emission_ms": list(self.first_emission_ms),
                        "response_to_audio_ms": list(self.response_to_audio_ms),
                        "definition": "fine frase utente -> primo campione audio della risposta"},
                "wake": {"accepted": len(self.wake_times), "accepted_at": list(self.wake_times),
                         "echo_ignored": self.echo_ignored, "commands": self.commands},
            }
