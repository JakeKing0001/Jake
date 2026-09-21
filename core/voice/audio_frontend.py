"""Pre-elaborazione del microfono: cancellazione d'eco, riduzione del rumore, controllo del
guadagno (F2.4.1, F2.4.2).

Quando Jake parla dagli altoparlanti, il microfono sente la sua voce: senza cancellare quell'eco
non si puo' distinguere "l'utente mi interrompe" da "sto sentendo me stesso" (barge-in, F2.4.3).
La cancellazione usa la tecnica classica: si conosce esattamente cio' che e' stato mandato agli
altoparlanti (il *riferimento*), un filtro adattivo NLMS impara come quel segnale arriva al
microfono (ritardo, attenuazione, riverbero della stanza) e ne sottrae la stima.

Tutto in numpy puro, nessun modello, nessun dispositivo: i test generano segnali sintetici con un
"percorso d'eco" noto e misurano di quanti dB l'eco cala (ERLE). Limiti dichiarati, non nascosti:
- cancella solo cio' che Jake stesso ha riprodotto: una TV accesa in stanza non e' nel riferimento
  e non viene rimossa (F2.4.6, benchmark);
- il filtro corto (`taps`) copre il riverbero, non il ritardo di un dispositivo Bluetooth (100-250
  ms): quello si stima con `estimate_delay` e si passa come `delay_samples`;
- senza riferimento (`push_reference` mai chiamato) non fa nulla: il microfono passa intatto."""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000


def estimate_delay(reference: np.ndarray, mic: np.ndarray, max_delay: int) -> int:
    """Ritardo (in campioni, >= 0) con cui `reference` compare in `mic`, per correlazione
    incrociata via FFT. 0 se non c'e' una correlazione significativa (nessun eco da allineare)."""
    n = min(len(reference), len(mic))
    if n < 256:
        return 0
    ref = np.asarray(reference[:n], dtype=np.float64)
    mic = np.asarray(mic[:n], dtype=np.float64)
    size = 1 << (2 * n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(mic, size) * np.conj(np.fft.rfft(ref, size)), size)[: max_delay + 1]
    energy = np.sqrt(np.sum(ref**2) * np.sum(mic**2))
    if energy == 0:
        return 0
    peak = int(np.argmax(corr))
    return peak if corr[peak] / energy > 0.05 else 0


def erle_db(echo_only: np.ndarray, residual: np.ndarray) -> float:
    """Echo Return Loss Enhancement: di quanti dB e' calata la potenza dell'eco."""
    before = float(np.mean(np.asarray(echo_only, dtype=np.float64) ** 2))
    after = float(np.mean(np.asarray(residual, dtype=np.float64) ** 2))
    if before <= 0:
        return 0.0
    return 10 * float(np.log10(before / max(after, 1e-20)))


class EchoCanceller:
    """AEC a filtro adattivo NLMS con protezione da "double talk" (test di Geigel): quando il
    microfono e' molto piu' forte di cio' che l'eco puo' essere - l'utente sta parlando - il filtro
    smette di adattarsi, altrimenti imparerebbe a cancellare la voce dell'utente."""

    def __init__(self, taps: int = 512, mu: float = 0.5, delay_samples: int = 0, geigel: float = 1.0, warmup_s: float = 0.4, max_reference_s: float = 30.0) -> None:
        self.taps = taps
        self.mu = mu
        self.delay_samples = delay_samples
        self.geigel = geigel
        self._warmup = int(warmup_s * SAMPLE_RATE)
        self._max_reference = int(max_reference_s * SAMPLE_RATE)
        self.reset()

    def reset(self) -> None:
        """Da chiamare all'inizio di ogni riproduzione: riferimento e posizione ripartono da zero."""
        self._reference = np.zeros(0, dtype=np.float32)
        self._ref_offset = 0  # indice assoluto del primo campione ancora nel buffer
        self._pos = 0  # indice assoluto del prossimo campione di microfono
        self._adapted = 0  # campioni di riferimento usati per adattarsi
        self._weights = np.zeros(self.taps, dtype=np.float64)

    def seek(self, position: int) -> None:
        """Il microfono e' gia' a `position` campioni dall'inizio della riproduzione (serve a chi riparte
        con un filtro pulito a riproduzione in corso, vedi core/voice/playback_aec.py)."""
        self._pos = max(0, int(position))

    def push_reference(self, samples: np.ndarray) -> None:
        """Audio mandato agli altoparlanti (float32 mono a 16 kHz), nell'ordine in cui esce."""
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        self._reference = np.concatenate([self._reference, samples])
        excess = len(self._reference) - self._max_reference
        if excess > 0:
            self._reference = self._reference[excess:]
            self._ref_offset += excess

    def _reference_window(self, start: int, length: int) -> np.ndarray:
        """Campioni di riferimento con indici assoluti [start, start+length), zero fuori dal buffer."""
        out = np.zeros(length, dtype=np.float64)
        lo = max(start, self._ref_offset)
        hi = min(start + length, self._ref_offset + len(self._reference))
        if hi > lo:
            out[lo - start:hi - start] = self._reference[lo - self._ref_offset:hi - self._ref_offset]
        return out

    def process(self, mic: np.ndarray) -> np.ndarray:
        mic = np.asarray(mic, dtype=np.float64).reshape(-1)
        n = len(mic)
        if n == 0 or len(self._reference) == 0:
            self._pos += n
            return mic.astype(np.float32)
        # per il campione di microfono k serve il riferimento fino all'indice k - ritardo
        window = self._reference_window(self._pos - self.delay_samples - (self.taps - 1), n + self.taps - 1)
        out = np.empty(n, dtype=np.float64)
        weights = self._weights
        eps = 1e-6
        for i in range(n):
            x = window[i:i + self.taps]
            estimate = float(np.dot(weights, x))
            error = mic[i] - estimate
            out[i] = error
            power = float(np.dot(x, x))
            # Double talk (Geigel): il microfono supera cio' che l'eco puo' essere, quindi c'e' anche
            # la voce dell'utente e adattarsi la cancellerebbe. Nella fase iniziale si adatta sempre:
            # con un filtro a zero ogni frame sembrerebbe "double talk" e non convergerebbe mai.
            if power > 1e-9 and (self._adapted < self._warmup or abs(mic[i]) <= self.geigel * float(np.max(np.abs(x))) + 1e-4):
                weights += (self.mu * error / (power + eps)) * x
                self._adapted += 1
        self._weights = weights
        self._pos += n
        return out.astype(np.float32)

    def calibrate(self, reference: np.ndarray, mic: np.ndarray, max_delay_s: float = 0.4) -> int:
        """Stima il ritardo altoparlante->microfono da un tratto in cui Jake parla e l'utente tace
        e lo imposta. Ritorna i campioni di ritardo."""
        self.delay_samples = estimate_delay(reference, mic, int(max_delay_s * SAMPLE_RATE))
        return self.delay_samples


class NoiseSuppressor:
    """Sottrazione spettrale con rumore di fondo stimato per minimi (nessuna calibrazione a parte):
    finestre da 512 campioni con 50% di sovrapposizione e radice di Hann in analisi e sintesi, quindi
    la ricostruzione e' esatta dove il guadagno vale 1. Latenza: 256 campioni (16 ms). Un segnale
    perfettamente stazionario (un tono continuo) viene trattato come rumore: e' il limite noto di
    ogni stimatore a minimi, ed e' accettabile perche' il parlato non lo e'."""

    FRAME = 512
    HOP = 256

    def __init__(self, reduction_db: float = 12.0, over_subtraction: float = 1.3) -> None:
        self.floor_gain = 10 ** (-reduction_db / 20)
        self.over = over_subtraction
        self._window = np.sqrt(np.hanning(self.FRAME + 1)[:-1])
        self.reset()

    def reset(self) -> None:
        self._noise: np.ndarray | None = None
        self._smooth: np.ndarray | None = None
        self.latency_samples = self.HOP  # ritardo attuale tra ingresso e uscita
        self._in = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.FRAME - self.HOP, dtype=np.float64)
        self._out = np.zeros(self.HOP, dtype=np.float64)  # latenza iniziale: un hop di silenzio
        self._frames_seen = 0

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float64).reshape(-1)
        want = len(x)
        self._in = np.concatenate([self._in, x])
        produced = []
        while len(self._in) >= self.FRAME:
            frame = self._in[:self.FRAME] * self._window
            self._in = self._in[self.HOP:]
            spectrum = np.fft.rfft(frame)
            magnitude = np.abs(spectrum)
            # La magnitudine di un frame di solo rumore oscilla molto tra un frame e l'altro: il
            # suo minimo istantaneo e' molto sotto la media e sottostimerebbe il rumore (verificato:
            # senza lisciatura la riduzione era nulla). Si liscia prima e si insegue il minimo del
            # lisciato, che sale lentamente e scende subito: segue il rumore, non il parlato.
            self._smooth = magnitude.copy() if self._smooth is None else 0.85 * self._smooth + 0.15 * magnitude
            if self._noise is None:
                self._noise = self._smooth.copy()
            else:
                self._noise = np.where(self._smooth < self._noise, self._smooth, self._noise * 1.004 + 1e-12)
            self._frames_seen += 1
            gain = np.clip(1.0 - self.over * self._noise / np.maximum(magnitude, 1e-12), self.floor_gain, 1.0)
            cleaned = np.fft.irfft(spectrum * gain, self.FRAME) * self._window
            cleaned[: self.FRAME - self.HOP] += self._overlap
            produced.append(cleaned[: self.HOP])
            self._overlap = cleaned[self.HOP:].copy()
        if produced:
            self._out = np.concatenate([self._out] + produced)
        if len(self._out) >= want:
            result, self._out = self._out[:want], self._out[want:]
        else:  # all'inizio, prima che ci siano abbastanza campioni: completa con silenzio
            padding = want - len(self._out)
            self.latency_samples += padding
            result = np.concatenate([np.zeros(padding), self._out])
            self._out = np.zeros(0, dtype=np.float64)
        return result.astype(np.float32)


class AutoGain:
    """Controllo automatico del guadagno: porta il livello verso `target_rms` con variazioni lente
    (attacco piu' rapido del rilascio, per non "pompare") e un tetto di `max_gain_db`. Non amplifica
    mai i frame sotto `gate_rms`: un fruscio in un momento di silenzio non deve diventare forte."""

    def __init__(self, target_rms: float = 0.08, max_gain_db: float = 18.0, gate_rms: float = 0.004,
                 attack: float = 0.3, release: float = 0.05) -> None:
        self.target = target_rms
        self.max_gain = 10 ** (max_gain_db / 20)
        self.gate = gate_rms
        self.attack = attack
        self.release = release
        self.gain = 1.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0
        if rms >= self.gate:
            desired = min(self.max_gain, self.target / rms)
            rate = self.attack if desired < self.gain else self.release  # scende in fretta, sale piano
            self.gain += (desired - self.gain) * rate
        return np.clip(x * self.gain, -1.0, 1.0).astype(np.float32)


class AudioFrontEnd:
    """Catena AEC -> riduzione rumore -> AGC, ciascun blocco escludibile (F2.4.2 "bypass
    configurabile"). Con tutti i blocchi esclusi `process` ritorna lo STESSO array in ingresso."""

    def __init__(self, aec: bool = True, noise_suppression: bool = True, agc: bool = True,
                 echo_canceller: EchoCanceller | None = None) -> None:
        self.echo_canceller = (echo_canceller or EchoCanceller()) if aec else None
        self.noise_suppressor = NoiseSuppressor() if noise_suppression else None
        self.auto_gain = AutoGain() if agc else None

    def push_reference(self, samples: np.ndarray) -> None:
        if self.echo_canceller is not None:
            self.echo_canceller.push_reference(samples)

    def process(self, mic: np.ndarray) -> np.ndarray:
        if self.echo_canceller is None and self.noise_suppressor is None and self.auto_gain is None:
            return mic
        out = np.asarray(mic, dtype=np.float32).reshape(-1)
        if self.echo_canceller is not None:
            out = self.echo_canceller.process(out)
        if self.noise_suppressor is not None:
            out = self.noise_suppressor.process(out)
        if self.auto_gain is not None:
            out = self.auto_gain.process(out)
        return out
