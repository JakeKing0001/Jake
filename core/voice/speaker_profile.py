"""Profili vocali opt-in, cancellabili, usati come INDIZIO (F2.7.1, F2.7.2, F2.7.3).

Cosa e' e cosa non e':
- e' un'impronta sonora grossolana (MFCC medi + altezza media della voce) calcolata in numpy, senza
  modelli scaricati. Riesce a distinguere voci molto diverse ma NON e' un riconoscitore da produzione:
  la soglia qui sotto e' stata tarata su due sole voci di sintesi (leave-one-out 12/12 con
  arruolamento su 5 frasi) e va ritarata su arruolamenti veri; una voce registrata (replay) e un
  imitatore la superano;
- per questo il risultato e' un `SpeakerHint` che serve solo a SCEGLIERE quale profilo usare, mai a
  concedere un permesso: azioni DESTRUCTIVE/ADMIN passano comunque da `AuthGate` (vedi core/profiles.py e
  "Cose che Jake non deve mai diventare" nella roadmap). Nel dubbio il sistema CHIEDE chi sta parlando;
- e' opt-in: senza un arruolamento esplicito non esiste alcun profilo, e si arruola solo passando
  `consent=True`. L'archivio contiene solo vettori di numeri, mai audio, e `delete` li rimuove davvero
  (file riscritto)."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.logger import get_logger

SAMPLE_RATE = 16000
MIN_SPEECH_S = 1.0
MIN_ENROLL_SAMPLES = 3
DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "speaker_profiles.json"

# Soglie PROVVISORIE (vedi il docstring del modulo): distanza euclidea tra impronta e centroide.
ACCEPT_DISTANCE = 4.0  # sopra: nessun profilo e' abbastanza vicino
MARGIN_RATIO = 0.75  # con piu' profili, il migliore deve stare a <= 75% della distanza del secondo


def _mel_filterbank(n_mels: int = 26, n_fft: int = 512, fmin: float = 100.0, fmax: float = 7000.0) -> np.ndarray:
    def hz_to_mel(f: float) -> float:
        return 2595 * np.log10(1 + f / 700)

    def mel_to_hz(m: np.ndarray) -> np.ndarray:
        return 700 * (10 ** (m / 2595) - 1)

    points = mel_to_hz(np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2))
    bins = np.floor((n_fft + 1) * points / SAMPLE_RATE).astype(int)
    bank = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        left, centre, right = bins[m - 1], bins[m], bins[m + 1]
        for k in range(left, centre):
            bank[m - 1, k] = (k - left) / max(centre - left, 1)
        for k in range(centre, right):
            bank[m - 1, k] = (right - k) / max(right - centre, 1)
    return bank


_FILTERBANK = _mel_filterbank()
_DCT = np.cos(np.pi * np.arange(13)[:, None] * (np.arange(26)[None, :] + 0.5) / 26) * np.sqrt(2 / 26)


def extract_features(audio: np.ndarray) -> np.ndarray | None:
    """Impronta di 25 numeri (12 MFCC medi, 12 deviazioni standard, altezza mediana/100) dai frame piu'
    energici (la meta' alta: dove c'e' voce). None se l'audio e' troppo breve, o se non contiene voce
    (rumore: nessun frame con altezza tonale riconoscibile)."""
    x = np.asarray(audio, dtype=np.float64).reshape(-1)
    if len(x) < MIN_SPEECH_S * SAMPLE_RATE:
        return None
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    frame_len, hop = 400, 160
    window = np.hamming(frame_len)
    frames = np.array([x[i:i + frame_len] * window for i in range(0, len(x) - frame_len, hop)])
    energy = np.log(np.sum(frames**2, axis=1) + 1e-9)
    loud = frames[energy > np.percentile(energy, 50)]
    if len(loud) < 10:
        return None
    mfcc = (np.log(np.abs(np.fft.rfft(loud, 512)) ** 2 @ _FILTERBANK.T + 1e-9) @ _DCT.T)[:, 1:]
    pitches = []
    low, high = int(SAMPLE_RATE / 400), int(SAMPLE_RATE / 60)
    for frame in loud:
        autocorr = np.correlate(frame, frame, "full")[len(frame) - 1:]
        if autocorr[0] > 0 and autocorr[low:high].max() / autocorr[0] > 0.3:
            pitches.append(SAMPLE_RATE / (low + int(np.argmax(autocorr[low:high]))))
    if len(pitches) < 5:
        return None  # nessuna voce riconoscibile (rumore, silenzio)
    return np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0), [float(np.median(pitches)) / 100.0]])


@dataclass
class SpeakerProfile:
    profile_id: str
    display_name: str
    centroid: list[float]
    samples: int
    enrolled_at: float
    consent_statement: str = "opt-in esplicito"


@dataclass(frozen=True)
class SpeakerHint:
    """Esito di `identify`. NON e' un'autenticazione."""

    profile_id: str | None
    confidence: str  # "high" | "low" | "none"
    distance: float | None = None
    second_distance: float | None = None
    candidates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_disambiguation(self) -> bool:
        """True se il sistema deve chiedere chi sta parlando (F2.7.3)."""
        return self.confidence != "high"


class SpeakerProfileStore:
    def __init__(self, path: Path = DEFAULT_PATH, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._logger = get_logger()

    def _load(self) -> dict[str, SpeakerProfile]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {pid: SpeakerProfile(**entry) for pid, entry in raw["profiles"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            self._logger.exception("Archivio profili vocali illeggibile: nessun profilo attivo")
            return {}

    def _save(self, profiles: dict[str, SpeakerProfile]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "profiles": {pid: vars(profile) for pid, profile in profiles.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def profiles(self) -> list[SpeakerProfile]:
        return list(self._load().values())

    def get(self, profile_id: str) -> SpeakerProfile | None:
        return self._load().get(profile_id)

    def enroll(self, profile_id: str, display_name: str, samples: list[np.ndarray], consent: bool) -> SpeakerProfile:
        """Crea (o rimpiazza) un profilo da almeno 3 impronte. Senza `consent=True` rifiuta: l'arruolamento
        e' una scelta esplicita di chi verra' riconosciuto, mai un effetto collaterale."""
        if consent is not True:
            raise PermissionError("l'arruolamento vocale richiede un consenso esplicito (consent=True)")
        if not profile_id.strip() or not display_name.strip():
            raise ValueError("servono un id e un nome")
        valid = [np.asarray(s, dtype=np.float64) for s in samples if s is not None]
        if len(valid) < MIN_ENROLL_SAMPLES:
            raise ValueError(f"servono almeno {MIN_ENROLL_SAMPLES} campioni validi (frasi diverse), ricevuti {len(valid)}")
        profile = SpeakerProfile(profile_id.strip(), display_name.strip(), np.mean(valid, axis=0).tolist(), len(valid), self._clock())
        profiles = self._load()
        profiles[profile.profile_id] = profile
        self._save(profiles)
        return profile

    def delete(self, profile_id: str) -> bool:
        profiles = self._load()
        if profile_id not in profiles:
            return False
        del profiles[profile_id]
        if profiles:
            self._save(profiles)
        else:
            self.path.unlink(missing_ok=True)  # nessun profilo: nessun file, non un file vuoto
        return True

    def delete_all(self) -> int:
        count = len(self._load())
        self.path.unlink(missing_ok=True)
        return count


def identify(features: np.ndarray | None, profiles: list[SpeakerProfile]) -> SpeakerHint:
    """Il profilo piu' vicino, se abbastanza vicino e (con piu' profili) abbastanza distante dal secondo.
    Altrimenti "low" (un candidato plausibile ma incerto: si chiede conferma) o "none"."""
    if features is None or not profiles:
        return SpeakerHint(None, "none")
    vector = np.asarray(features, dtype=np.float64)
    ranked = sorted(
        ((float(np.linalg.norm(vector - np.asarray(p.centroid))), p.profile_id) for p in profiles),
    )
    best_distance, best_id = ranked[0]
    second_distance = ranked[1][0] if len(ranked) > 1 else None
    candidates = tuple(pid for _, pid in ranked[:2])
    if best_distance > ACCEPT_DISTANCE:
        return SpeakerHint(None, "none", best_distance, second_distance, candidates)
    if second_distance is not None and best_distance > MARGIN_RATIO * second_distance:
        return SpeakerHint(best_id, "low", best_distance, second_distance, candidates)
    return SpeakerHint(best_id, "high", best_distance, second_distance, candidates)


def disambiguation_question(hint: SpeakerHint, profiles: list[SpeakerProfile]) -> str:
    """La domanda da fare quando `hint.needs_disambiguation`: mai indovinare tra due persone."""
    names = {p.profile_id: p.display_name for p in profiles}
    named = [names[pid] for pid in hint.candidates if pid in names]
    if hint.confidence == "low" and named:
        return f"Sei {' o '.join(named)}?" if len(named) > 1 else f"Sei {named[0]}?"
    if named:
        return "Non ho riconosciuto la voce: chi sta parlando?"
    return "Chi sta parlando?"
