"""Benchmark per il riconoscimento vocale (F0, vedi fase F0 in ROADMAP.md): latenza di
trascrizione di WhisperSttProvider (core/voice/stt_provider.py) su un file audio reale.

A differenza degli altri benchmark in questa cartella, QUESTO non genera dati da solo: il
progetto non versiona file audio (nessuna registrazione di voce nel repository, per scelta di
privacy) e non c'e' hardware microfono utilizzabile in modo headless/riproducibile in questo
ambiente. Richiede un file .wav passato esplicitamente:

    python -m benchmarks.bench_stt --audio percorso/a/una/registrazione.wav [--device cuda|cpu]

Senza --audio il programma spiega perche' e termina, invece di inventare una latenza mai
misurata su un file mai esistito."""
import argparse
import sys
import tempfile
import time
from pathlib import Path

from benchmarks._report import latency_stats, save_report
from benchmarks.voice_corpus import SAMPLE_RATE, CorpusEntry, corpus_hash, manifest, render_tts_entries
from benchmarks.voice_metrics import hardware_profile, word_error_rate

TTS_PHRASES = [
    "apri spotify", "che ore sono", "metti un timer di dieci minuti", "cerca su youtube un tutorial di blender",
    "alza il volume al cinquanta per cento",
]


def run(audio_path: Path, device: str = None, repeats: int = 3) -> dict:
    from core.voice.stt_provider import WhisperSttProvider

    provider = WhisperSttProvider(device=device)
    latencies_ms = []
    text = ""
    for _ in range(repeats):
        started = time.perf_counter()
        text = provider.transcribe(str(audio_path))
        latencies_ms.append((time.perf_counter() - started) * 1000)

    return {
        "audio_file": str(audio_path), "model_size": provider.model_size, "device": provider.device,
        "compute_type": provider.compute_type, "repeats": repeats, "latency": latency_stats(latencies_ms),
        "transcribed_text": text,
    }


def run_corpus(provider, entries: list[CorpusEntry], repeats: int = 1) -> dict:
    """WER e latenza di `provider` (qualunque oggetto con transcribe(audio, sample_rate)) sulle clip
    che hanno un testo di riferimento (F2.1.3/F2.1.5). L'audio arriva dal corpus, mai da file
    personali; nel report finiscono solo testi, WER, latenze e hash (F2.1.4)."""
    per_clip, latencies_ms, errors = [], [], []
    for entry in entries:
        if entry.ground_truth_text is None:
            continue
        audio = entry.pcm.astype("float32") / 32768.0
        hypothesis = ""
        clip_latencies = []
        for _ in range(repeats):
            started = time.perf_counter()
            hypothesis = provider.transcribe(audio, SAMPLE_RATE)
            clip_latencies.append((time.perf_counter() - started) * 1000)
        wer = word_error_rate(entry.ground_truth_text, hypothesis)
        latencies_ms.extend(clip_latencies)
        if wer is not None:
            errors.append(wer)
        per_clip.append({
            "id": entry.id, "reference": entry.ground_truth_text, "hypothesis": hypothesis, "wer": wer,
            "latency": latency_stats(clip_latencies),
        })
    return {
        "corpus": {"hash": corpus_hash(entries), "clips": len(per_clip)},
        "mean_wer": round(sum(errors) / len(errors), 4) if errors else None,
        "latency": latency_stats(latencies_ms),
        "per_clip": per_clip,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audio", type=Path, default=None, help="file .wav da trascrivere (obbligatorio)")
    parser.add_argument("--device", choices=["cuda", "cpu"], default=None, help="default: auto (GPU se disponibile)")
    parser.add_argument("--tts-corpus", action="store_true", help="misura WER su frasi pronunciate da una voce SAPI locale (nessun file audio personale)")
    parser.add_argument("--repeats", type=int, default=3, help="ripetizioni per la statistica di latenza")
    args = parser.parse_args()

    if args.tts_corpus:
        from core.voice.stt_provider import WhisperSttProvider

        with tempfile.TemporaryDirectory() as tmp:
            entries = render_tts_entries(TTS_PHRASES, Path(tmp))
        if not entries:
            print("Nessuna voce italiana SAPI disponibile: impossibile costruire il corpus TTS.", file=sys.stderr)
            raise SystemExit(1)
        provider = WhisperSttProvider(device=args.device)
        report = run_corpus(provider, entries, args.repeats)
        report.update({"model_size": provider.model_size, "device": provider.device, "hardware": hardware_profile(provider.device == "cuda")})
        report["manifest"] = manifest(entries)
        print(f"WER medio: {report['mean_wer']}  latenza p50={report['latency'].get('p50_ms')} ms  p95={report['latency'].get('p95_ms')} ms")
        for clip in report["per_clip"]:
            print(f"  {clip['id']}: WER={clip['wer']}  {clip['reference']!r} -> {clip['hypothesis']!r}")
        print(f"\nReport salvato in {save_report('stt_corpus_' + report['hardware']['profile'], report)}")
        return

    if args.audio is None or not args.audio.is_file():
        print(
            "Serve un file audio reale: `python -m benchmarks.bench_stt --audio registrazione.wav`.\n"
            "Il repository non ne versiona nessuno (niente registrazioni di voce per scelta di "
            "privacy) e non c'e' un microfono utilizzabile qui in modo headless: questo benchmark "
            "non puo' generarsi da solo i propri dati, va lanciato a mano con una registrazione vera.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"Benchmark STT: {args.repeats} trascrizioni di {args.audio}...")
    report = run(args.audio, device=args.device, repeats=args.repeats)

    print(f"\nModello: {report['model_size']} su {report['device']} ({report['compute_type']})")
    print(f"Latenza: p50={report['latency'].get('p50_ms')} ms  p95={report['latency'].get('p95_ms')} ms")
    print(f"Trascrizione: {report['transcribed_text']!r}")

    path = save_report("stt", report)
    print(f"\nReport salvato in {path}")


if __name__ == "__main__":
    main()
