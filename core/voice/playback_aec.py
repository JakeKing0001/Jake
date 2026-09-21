"""Cancellazione d'eco legata a una riproduzione (F2.4.1 nel percorso reale).

`audio_frontend.EchoCanceller` sa cancellare l'eco se conosce il riferimento (cosa e' uscito dagli
altoparlanti) e il ritardo altoparlante->microfono. In produzione nessuno dei due e' dato:
- il riferimento arriva dal provider TTS, che lo pubblica quando riproduce (`reference_sink`);
- il ritardo dipende dal dispositivo (cuffie, Bluetooth...) e non e' noto in anticipo: lo si stima da
  ogni riproduzione, nel primo secondo dopo che l'audio di Jake e' cominciato, quando di solito parla
  solo lui.

Il riferimento e' posizionato sulla linea del TEMPO, non accodato: un provider come Edge TTS
sintetizza una frase alla volta e tra due frasi c'e' un silenzio (rete, decodifica); se le frasi si
accodassero senza il vuoto, dalla seconda in poi il riferimento sarebbe sfasato rispetto al microfono
e l'AEC cancellerebbe il segnale sbagliato. Quando un provider chiama `push_reference` l'audio viene
piazzato alla posizione (ora - inizio riproduzione) e il tratto precedente resta silenzio.

`PlaybackAec` non e' "pronto" finche' non ha avuto abbastanza audio per stimare il ritardo: chi la usa
deve IGNORARE il barge-in in quel periodo (senza il ritardo l'eco non e' cancellato e sembrerebbe
una voce). Senza alcun riferimento (provider senza `reference_sink`, per esempio SAPI/pyttsx3)
`has_reference` resta falso e l'AEC non c'e': chi la usa lo sa e decide di conseguenza."""
from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np

from core.voice.audio_frontend import SAMPLE_RATE, EchoCanceller


def to_reference(samples: np.ndarray, rate: int) -> np.ndarray:
    """Porta campioni PCM (int16/int32/float) di qualunque frequenza a float32 mono 16 kHz in [-1, 1].
    Ricampionamento lineare: basta per un riferimento AEC (il filtro adattivo assorbe le piccole differenze)."""
    data = np.asarray(samples)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float32) / float(np.iinfo(data.dtype).max + 1)
    else:
        data = data.astype(np.float32)
    if rate != SAMPLE_RATE and len(data) > 1:
        target = max(1, int(round(len(data) * SAMPLE_RATE / rate)))
        data = np.interp(np.linspace(0, len(data) - 1, target), np.arange(len(data)), data).astype(np.float32)
    return data


class PlaybackAec:
    MAX_DELAY_S = 0.3  # ritardo massimo stimato (Bluetooth: 100-250 ms)
    # Il filtro vede solo il riferimento PIU' VECCHIO del ritardo impostato, per `taps` campioni: la prima
    # "presa" dell'eco deve cadere dentro quella finestra. Due cose la spostano: una stima per eccesso (anche
    # di un solo campione) e la differenza di latenza tra il momento in cui un provider pubblica il riferimento
    # e quello in cui l'audio esce davvero, che cambia da una frase all'altra (jitter). Misurato: con un margine
    # di 24 campioni e una seconda frase piazzata 10 ms in ritardo l'eco calava di ~7 dB invece di ~40. Si
    # sottrae quindi un margine di 20 ms (320 campioni): la finestra del filtro copre da 20 ms prima a 12 ms dopo la stima.
    DELAY_MARGIN = 320

    def __init__(self, calibration_s: float = 1.0, canceller: EchoCanceller | None = None,
                 clock: Callable[[], float] = time.monotonic, max_history_s: float = 20.0) -> None:
        self.calibration_samples = int(calibration_s * SAMPLE_RATE)
        self._canceller = canceller or EchoCanceller()
        self._clock = clock
        self._max_history = int(max_history_s * SAMPLE_RATE)
        self.reset()

    def reset(self) -> None:
        """Inizio di una nuova riproduzione: riferimento, filtro e calibrazione ripartono da zero."""
        self._canceller.reset()
        self._canceller.delay_samples = 0
        self._t0 = self._clock()
        self._reference = np.zeros(0, dtype=np.float32)
        self._reference_start: int | None = None  # indice del primo campione di riferimento non nullo
        self._history: list[np.ndarray] = []
        self._mic_seen = 0
        self.ready = False
        self.delay_samples = 0

    @property
    def has_reference(self) -> bool:
        return self._reference_start is not None

    @property
    def position(self) -> int:
        """Campioni di microfono visti dall'inizio della riproduzione."""
        return self._mic_seen

    def push_reference(self, samples: np.ndarray, rate: int = SAMPLE_RATE) -> None:
        """Audio che il provider sta per riprodurre ORA: viene piazzato sulla linea del tempo."""
        reference = to_reference(samples, rate)
        if reference.size == 0:
            return
        position = max(len(self._reference), int(round((self._clock() - self._t0) * SAMPLE_RATE)))
        if position > len(self._reference):
            gap = np.zeros(position - len(self._reference), dtype=np.float32)
            self._reference = np.concatenate([self._reference, gap])
            self._canceller.push_reference(gap)
        if self._reference_start is None:
            self._reference_start = position
        self._reference = np.concatenate([self._reference, reference])
        self._canceller.push_reference(reference)

    def reference_level(self, position: int, length: int) -> float:
        """RMS del riferimento nella finestra [position, position+length), 0 se non c'e' riferimento."""
        window = self._reference[position:position + length]
        return float(np.sqrt(np.mean(window.astype(np.float64) ** 2))) if window.size else 0.0

    def process(self, mic: np.ndarray) -> tuple[np.ndarray, bool]:
        """(residuo, pronto). Finche' non e' pronta il microfono passa intatto e `pronto` e' False."""
        mic = np.asarray(mic, dtype=np.float32).reshape(-1)
        self._mic_seen += len(mic)
        if not self.has_reference:
            return mic, False
        if not self.ready:
            self._history.append(mic)
            if sum(len(chunk) for chunk in self._history) > self._max_history:
                self._history.pop(0)  # non si accumula senza limite se la calibrazione non arriva mai
            ref_start = self._reference_start or 0
            needed = ref_start + self.calibration_samples + int(self.MAX_DELAY_S * SAMPLE_RATE)
            if self._mic_seen >= needed and len(self._reference) >= ref_start + self.calibration_samples:
                self._calibrate(ref_start)
            return mic, False
        residual = self._canceller.process(mic)
        return residual, True

    def _calibrate(self, ref_start: int) -> None:
        mic_all = np.concatenate(self._history)
        offset = self._mic_seen - len(mic_all)  # indice assoluto del primo campione in `mic_all`
        segment = mic_all[max(0, ref_start - offset):]
        reference = self._reference[ref_start:ref_start + self.calibration_samples]
        estimate = self._canceller.calibrate(reference, segment[:len(reference)], self.MAX_DELAY_S)
        self.delay_samples = max(0, estimate - self.DELAY_MARGIN)
        # Filtro pulito con il ritardo trovato, ADDESTRATO subito sul microfono gia' ascoltato (la calibrazione
        # ha ~1,3 s di audio con l'eco): senza questo avvio "a caldo" il filtro riparte da zero quando l'AEC
        # dovrebbe gia' lavorare, e l'eco cala di ~7 dB invece di ~18 nei primi secondi (misurato).
        reference_all = self._reference.copy()
        self._canceller.reset()
        self._canceller.delay_samples = self.delay_samples
        self._canceller.push_reference(reference_all)
        self._canceller.seek(offset)
        self._canceller.process(mic_all)  # l'uscita si scarta: serve solo ad adattare i coefficienti
        self._history = []
        self.ready = True
