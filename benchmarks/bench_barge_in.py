"""Benchmark offline del barge-in per famiglia di dispositivo (F2.4.6, F2.4.7).

Simula, senza microfono, il percorso altoparlante -> stanza -> microfono di quattro situazioni:
cuffie, altoparlante del portatile, Bluetooth (ritardo lungo) e TV accesa in sottofondo. Per ognuna
fa "parlare" Jake (riferimento) e misura, con e senza la cancellazione d'eco:

- quante volte una VERA interruzione dell'utente viene riconosciuta e dopo quanti ms (tempo di
  stop, obiettivo 300 ms);
- quante volte scatta un FALSO barge-in quando l'utente NON parla (solo eco e sottofondo).

Sono simulazioni con percorsi d'eco parametrici (ritardo, guadagno, riverbero), NON registrazioni:
dicono se l'algoritmo regge in condizioni note e dove si rompe (la TV, che non e' nel riferimento,
e' il caso in cui si rompe), non sostituiscono la prova su altoparlanti veri, che il criterio di
uscita di F2 richiede ancora ("almeno tre profili hardware").

    python -m benchmarks.bench_barge_in [--trials 3]"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np

from benchmarks._report import save_report
from benchmarks.voice_corpus import _speechlike
from benchmarks.voice_metrics import hardware_profile
from core.voice.audio_frontend import EchoCanceller, estimate_delay
from core.voice.barge_in import BargeInDetector

SR = 16000
FRAME = 480  # 30 ms


@dataclass(frozen=True)
class Scenario:
    name: str
    delay_ms: float
    gain: float  # quanto dell'uscita dell'altoparlante arriva al microfono
    reverb: tuple[float, ...] = (1.0, 0.4, 0.2, 0.1)
    tv_level: float = 0.0  # RMS di un parlato di sottofondo NON presente nel riferimento
    mic_noise: float = 0.002


SCENARIOS = [
    Scenario("headphones", 5, 0.03),
    Scenario("laptop_speaker", 15, 0.5),
    Scenario("bluetooth_180ms", 180, 0.35),
    Scenario("tv_background", 15, 0.5, tv_level=0.06),
]


def _speech(seconds: float, rng: np.random.Generator, f0: float, rms: float) -> np.ndarray:
    signal = _speechlike(seconds, rng, f0=f0).astype(np.float64) / 32768
    return signal * (rms / max(float(np.sqrt(np.mean(signal**2))), 1e-9))


def _echo(reference: np.ndarray, scenario: Scenario) -> np.ndarray:
    delay = int(scenario.delay_ms * SR / 1000)
    path = np.zeros(delay + len(scenario.reverb))
    for i, tap in enumerate(scenario.reverb):
        path[delay + i] = tap * scenario.gain
    return np.convolve(reference, path)[: len(reference)]


def simulate_trial(scenario: Scenario, seed: int, user_speaks: bool, aec: bool, seconds: float = 5.0, user_onset_s: float = 2.5, user_rms: float = 0.08) -> dict:
    import webrtcvad

    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    reference = np.concatenate([_speech(1.0, rng, f0, 0.1) for f0 in (120, 170, 140, 190, 130, 160)])[:n]
    mic = _echo(reference, scenario) + rng.standard_normal(n) * scenario.mic_noise
    if scenario.tv_level:
        mic = mic + _speech(seconds, rng, 230, scenario.tv_level)[:n]
    onset = int(user_onset_s * SR)
    if user_speaks:
        user = _speech(1.5, rng, 105, user_rms)
        mic[onset:onset + len(user)] += user

    canceller = None
    if aec:
        canceller = EchoCanceller()
        canceller.delay_samples = estimate_delay(reference[:SR], mic[:SR], int(0.4 * SR))  # 1 s iniziale: solo Jake parla
        canceller.push_reference(reference)
    vad = webrtcvad.Vad(2)
    detector = BargeInDetector()
    triggered_at = None
    for start in range(0, n - FRAME + 1, FRAME):
        frame = mic[start:start + FRAME].astype(np.float32)
        residual = canceller.process(frame) if canceller is not None else frame
        pcm = np.clip(residual * 32768, -32768, 32767).astype(np.int16)
        level = float(np.sqrt(np.mean(residual.astype(np.float64) ** 2)))
        ref_level = float(np.sqrt(np.mean(reference[start:start + FRAME] ** 2)))
        if detector.update(vad.is_speech(pcm.tobytes(), SR), level, ref_level, speaking=True):
            triggered_at = (start + FRAME) / SR
            break
    result = {"triggered": triggered_at is not None}
    if user_speaks:
        result["latency_ms"] = None if triggered_at is None else round(max(0.0, triggered_at - user_onset_s) * 1000)
    else:
        result["false_barge_in"] = triggered_at is not None
    return result


def run(trials: int = 3) -> dict:
    report = {"trials_per_cell": trials, "hardware": hardware_profile(cuda=False), "scenarios": []}
    for scenario in SCENARIOS:
        row = {"scenario": scenario.name}
        for aec in (True, False):
            hits, latencies, falses = 0, [], 0
            for seed in range(trials):
                interrupt = simulate_trial(scenario, seed, user_speaks=True, aec=aec)
                if interrupt["triggered"] and interrupt["latency_ms"] is not None:
                    hits += 1
                    latencies.append(interrupt["latency_ms"])
                falses += simulate_trial(scenario, 1000 + seed, user_speaks=False, aec=aec)["false_barge_in"]
            row["aec_on" if aec else "aec_off"] = {
                "interruptions_detected": f"{hits}/{trials}",
                "median_latency_ms": int(np.median(latencies)) if latencies else None,
                "false_barge_ins": f"{falses}/{trials}",
            }
        report["scenarios"].append(row)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    report = run(args.trials)
    print(f"{'scenario':<18}{'AEC':<6}{'interruzioni':<16}{'latenza mediana':<18}{'falsi barge-in'}")
    for row in report["scenarios"]:
        for key, label in (("aec_on", "on"), ("aec_off", "off")):
            cell = row[key]
            print(f"{row['scenario']:<18}{label:<6}{cell['interruptions_detected']:<16}{str(cell['median_latency_ms']) + ' ms':<18}{cell['false_barge_ins']}")
    print(f"\nReport salvato in {save_report('barge_in', report)}")


if __name__ == "__main__":
    main()
