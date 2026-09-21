"""Segmentazione del parlato in frasi, separata dall'acquisizione audio (F2.2.1).

Prima di questo modulo la macchina a stati "parlato/silenzio -> frase finita" viveva dentro
`VadListener.listen_for_utterances`, incollata al flusso di `sounddevice`: per provarla serviva un
finto `InputStream`, e il futuro runner offline dei benchmark voce (F2.1.3) non poteva riusarla
senza un microfono. Qui la logica e' pura: riceve un frame alla volta e un giudizio "e' parlato?"
gia' calcolato da chi la chiama, e restituisce la frase completa quando la considera finita.

Il buffer dei frame vive solo in RAM e viene svuotato appena una frase e' stata consegnata o
annullata (F2.2.5): nessun percorso di questo modulo scrive su disco."""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000


class UtteranceSegmenter:
    """Macchina a stati frame-per-frame. `feed()` ritorna un array float32 mono quando una frase
    e' terminata (silenzio sufficiente dopo il parlato, oppure lunghezza massima raggiunta),
    altrimenti None."""

    def __init__(self, silence_frames_needed: int, max_frames: int) -> None:
        self.silence_frames_needed = max(1, silence_frames_needed)
        self.max_frames = max(1, max_frames)
        self._frames: list[np.ndarray] = []
        self._silence_run = 0
        self._in_speech = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def buffered_frames(self) -> int:
        return len(self._frames)

    def reset(self) -> None:
        """Scarta qualunque frase parziale (es. Jake sta parlando e il microfono e' in mute: il
        frammento bufferizzato sarebbe la voce di Jake stessa)."""
        self._frames = []
        self._silence_run = 0
        self._in_speech = False

    def feed(self, frame: np.ndarray, is_speech: bool) -> np.ndarray | None:
        if is_speech:
            self._frames.append(frame)
            self._silence_run = 0
            self._in_speech = True
            return None
        if not self._in_speech:
            return None
        self._frames.append(frame)  # include un po' di coda dopo il parlato
        self._silence_run += 1
        if self._silence_run >= self.silence_frames_needed or len(self._frames) >= self.max_frames:
            return self._finalize()
        return None

    def seed(self, frames: list[np.ndarray]) -> None:
        """Comincia una frase GIA' in corso, con questi frame come parlato iniziale (pre-roll di un
        barge-in: l'utente parla da qualche istante e quell'inizio non deve andare perso)."""
        self._frames = [np.asarray(frame).copy() for frame in frames]
        self._silence_run = 0
        self._in_speech = bool(self._frames)

    def flush(self) -> np.ndarray | None:
        """Chiude a forza la frase in corso (fine del flusso). None se non c'era parlato."""
        if not self._in_speech:
            self.reset()
            return None
        return self._finalize()

    def _finalize(self) -> np.ndarray:
        utterance = to_float32(self._frames)
        self.reset()
        return utterance


def to_float32(frames: list[np.ndarray]) -> np.ndarray:
    pcm16 = np.concatenate(frames, axis=0).reshape(-1)
    return pcm16.astype(np.float32) / 32768.0
