"""Benchmark offline del rilevamento voce (F2.1.3): fa passare il corpus di benchmarks/voice_corpus.py
attraverso lo stesso VAD (webrtcvad) e lo stesso segmentatore (core/voice/utterance_segmenter.py)
che usa l'ascolto continuo, senza microfono, e confronta il risultato con le annotazioni.

Misura, per clip e in totale: falsi accettati (rumore/silenzio dichiarati parlato), falsi rifiutati
(parlato scartato), numero di frasi trovate rispetto a quelle attese, tempo di elaborazione per
frame. NON dice se webrtcvad "va bene": il corpus contiene apposta rumore forte e un tono, che un
VAD a energia dichiara parlato - il numero e' li' per essere guardato, non per essere nascosto.

    python -m benchmarks.bench_vad [--aggressiveness 0-3] [--silence-ms 700] [--with-tts]

Il report contiene solo metriche, hash del corpus e profilo hardware (F2.1.4/F2.1.5), mai audio."""
from __future__ import annotations

import argparse
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from benchmarks._report import latency_stats, save_report
from benchmarks.voice_corpus import (
    FRAME_MS, SAMPLE_RATE, CorpusEntry, build_synthetic_corpus, corpus_hash, frame_truth, frames,
    manifest, render_tts_entries,
)
from benchmarks.voice_metrics import confusion, hardware_profile, merge_confusions
from core.voice.utterance_segmenter import UtteranceSegmenter

IsSpeech = Callable[[bytes], bool]


def evaluate_entry(entry: CorpusEntry, is_speech: IsSpeech, silence_ms: int = 700, max_utterance_s: float = 12.0) -> dict:
    entry_frames = frames(entry)
    segmenter = UtteranceSegmenter(max(1, silence_ms // FRAME_MS), int(max_utterance_s * 1000 // FRAME_MS))
    predicted: list[bool] = []
    utterances = 0
    per_frame_ms: list[float] = []
    for frame in entry_frames:
        started = time.perf_counter()
        decision = bool(is_speech(frame.tobytes()))
        per_frame_ms.append((time.perf_counter() - started) * 1000)
        predicted.append(decision)
        if segmenter.feed(frame, decision) is not None:
            utterances += 1
    if segmenter.flush() is not None:
        utterances += 1
    return {
        "id": entry.id,
        "frames": len(entry_frames),
        "confusion": confusion(frame_truth(entry), predicted),
        "utterances_found": utterances,
        "utterances_expected": entry.expected_utterances(silence_ms / 1000),
        "frame_latency": latency_stats(per_frame_ms),
    }


def _webrtc_detector_factory(aggressiveness: int) -> Callable[[], IsSpeech]:
    import webrtcvad

    def make() -> IsSpeech:
        # webrtcvad adatta un modello di rumore man mano che riceve frame: riusare la stessa
        # istanza tra clip farebbe dipendere il risultato di una clip da quelle precedenti (verificato:
        # dopo il rumore forte, il fruscio di stanza successivo veniva dichiarato tutto parlato).
        # Un'istanza nuova per clip rende ogni misura indipendente dall'ordine del corpus.
        vad = webrtcvad.Vad(aggressiveness)
        return lambda pcm: vad.is_speech(pcm, SAMPLE_RATE)

    return make


def run(
    entries: list[CorpusEntry], aggressiveness: int = 2, silence_ms: int = 700,
    make_detector: Callable[[], IsSpeech] | None = None,
) -> dict:
    make = make_detector or _webrtc_detector_factory(aggressiveness)
    results = [evaluate_entry(entry, make(), silence_ms) for entry in entries]
    return {
        "aggressiveness": aggressiveness,
        "silence_ms": silence_ms,
        "corpus": {"hash": corpus_hash(entries), "clips": len(entries)},
        "hardware": hardware_profile(cuda=False),  # webrtcvad e' solo CPU: la baseline e' sempre "cpu"
        "overall": merge_confusions([r["confusion"] for r in results]),
        "utterance_count_exact": sum(1 for r in results if r["utterances_found"] == r["utterances_expected"]),
        "per_clip": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aggressiveness", type=int, choices=[0, 1, 2, 3], default=2)
    parser.add_argument("--silence-ms", type=int, default=700)
    parser.add_argument("--with-tts", action="store_true", help="aggiunge clip pronunciate da una voce SAPI locale, se c'e'")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    entries = build_synthetic_corpus(args.seed) if args.seed is not None else build_synthetic_corpus()
    if args.with_tts:
        with tempfile.TemporaryDirectory() as tmp:
            entries += render_tts_entries(["apri spotify", "che ore sono", "metti un timer di dieci minuti"], Path(tmp))
    report = run(entries, args.aggressiveness, args.silence_ms)
    report["manifest"] = manifest(entries)

    overall = report["overall"]
    print(f"Corpus {report['corpus']['hash'][:12]} - {report['corpus']['clips']} clip, aggressivita' {args.aggressiveness}")
    print(f"Falsi accettati: {overall['false_accept_rate']}  Falsi rifiutati: {overall['false_reject_rate']}")
    for r in report["per_clip"]:
        marker = "ok " if r["utterances_found"] == r["utterances_expected"] else "!! "
        print(f"  {marker}{r['id']:<26} frasi {r['utterances_found']}/{r['utterances_expected']}  "
              f"FA={r['confusion']['false_accept_rate']} FR={r['confusion']['false_reject_rate']}")
    path = save_report(f"vad_{report['hardware']['profile']}", report)
    print(f"\nReport salvato in {path}")


if __name__ == "__main__":
    main()
