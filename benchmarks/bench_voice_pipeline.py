"""F2.5 — misura separata della pipeline vocale REALE di produzione.

Misura, per ogni risposta, quattro tempi distinti invece di un unico "Jake e' lento":

- `answer_ms`: `JakeCore.answer()` (router/NLU e, per una domanda aperta, il modello), opzionale;
- `base_tts_ms`: sintesi della voce di base (Edge `it-IT-DiegoNeural`, o la voce offline);
- `rvc_ms`: conversione del timbro RVC (solo con `voice_character`);
- `first_audio_ms`: da `speak()` al primo campione mandato all'uscita audio;
- `gap_ms`: silenzio tra la fine di un chunk e l'inizio del successivo.

Usa la stessa costruzione dei provider di `main.py` (`_build_tts_provider`,
`_build_character_tts_provider`), quindi misura la configurazione in `config/settings.json` - con
`voice_character` impostato misura Character/RVC, non la voce base. La riproduzione e' SIMULATA
(attesa pari alla durata dell'audio, nessun suono dagli altoparlanti): i tempi di rete, sintesi e
conversione sono quelli veri, e la pausa tra chunk dipende solo da loro. `answer()` gira con
`private_mode` attivo: nessuna frase del benchmark finisce in memoria o cronologia.

    python -m benchmarks.bench_voice_pipeline --repeats 5
    python -m benchmarks.bench_voice_pipeline --repeats 5 --with-core
    python -m benchmarks.bench_voice_pipeline --no-character   # solo voce base, per confronto
"""
import argparse
import io
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks._report import latency_stats, save_report  # noqa: E402

SIMPLE_RESPONSE = "Sono le dieci e un quarto."
LONG_RESPONSE = (
    "Ho aperto il documento che mi hai chiesto. Dentro ci sono tre sezioni: introduzione, metodo e "
    "risultati. I risultati mostrano un miglioramento del dodici per cento rispetto alla prova "
    "precedente. Vuoi che ti legga il riassunto completo?"
)
CORE_PROMPTS = ("che ore sono", "spiegami in una frase cos'e' un buco nero")


class Timeline:
    """Eventi di UNA chiamata a speak(): sintesi, conversioni, inizio/fine di ogni chunk."""

    def __init__(self) -> None:
        self.synth_ms: list[float] = []
        self.rvc_ms: list[float] = []
        self.plays: list[tuple[float, float]] = []
        self.reset()

    def reset(self) -> None:
        # Svuotate sul posto: i cronometri agganciati al provider tengono un riferimento a queste liste.
        self.started = time.perf_counter()
        self.synth_ms.clear()
        self.rvc_ms.clear()
        self.plays.clear()

    def first_audio_ms(self) -> float | None:
        return (self.plays[0][0] - self.started) * 1000 if self.plays else None

    def gaps_ms(self) -> list[float]:
        return [(self.plays[i + 1][0] - self.plays[i][1]) * 1000 for i in range(len(self.plays) - 1)]


def _timed(timeline_list: list[float], fn):
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            timeline_list.append((time.perf_counter() - started) * 1000)
    return wrapper


def _wav_seconds(audio_bytes: bytes) -> float:
    with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
        return wav_file.getnframes() / float(wav_file.getframerate() or 1)


def instrument(provider, timeline: Timeline) -> str:
    """Aggancia i cronometri al provider costruito come in produzione. Ritorna il nome della
    pipeline misurata."""
    from core.voice.character_tts_provider import CharacterTtsProvider

    def simulated_play_wav(audio_bytes):
        start = time.perf_counter()
        time.sleep(_wav_seconds(audio_bytes))
        timeline.plays.append((start, time.perf_counter()))

    if isinstance(provider, CharacterTtsProvider):
        base = provider.base_tts_provider
        base.synthesize_to_file = _timed(timeline.synth_ms, base.synthesize_to_file)
        client = provider.server_manager.client
        client.convert = _timed(timeline.rvc_ms, client.convert)
        provider._play = simulated_play_wav
        return f"character_rvc({provider.server_manager.model_name}) + {type(base).__name__}"

    if hasattr(provider, "_synthesize") and hasattr(provider, "_decode_mp3"):  # Edge
        from core.voice.edge_tts_provider import SAMPLE_RATE

        provider._synthesize = _timed(timeline.synth_ms, provider._synthesize)

        def simulated_play_pcm(pcm):
            start = time.perf_counter()
            time.sleep(len(pcm) / SAMPLE_RATE)
            timeline.plays.append((start, time.perf_counter()))

        provider._play = simulated_play_pcm
        return f"{type(provider).__name__}({getattr(provider, 'voice', '?')})"
    raise SystemExit(f"Provider {type(provider).__name__} non strumentabile: nessuna riproduzione separabile")


def build_provider(use_character: bool):
    import main
    from core.config import Config

    config = Config()
    tts = main._build_tts_provider(config)
    manager = None
    if use_character:
        name = main._character_name_from_args(config)
        if name:
            tts, manager = main._build_character_tts_provider(tts, name)
    return tts, manager, config


def measure_tts(provider, timeline: Timeline, text: str, repeats: int) -> dict:
    first, gaps, synth, rvc, chunks = [], [], [], [], []
    for _ in range(repeats):
        timeline.reset()
        provider.speak(text)
        if timeline.first_audio_ms() is not None:
            first.append(timeline.first_audio_ms())
        gaps.extend(timeline.gaps_ms())
        synth.extend(timeline.synth_ms)
        rvc.extend(timeline.rvc_ms)
        chunks.append(len(timeline.plays))
    return {
        "text_chars": len(text),
        "chunks_per_response": chunks,
        "first_audio": latency_stats(first),
        "gap_between_chunks": latency_stats(gaps),
        "base_tts_per_chunk": latency_stats(synth),
        "rvc_per_chunk": latency_stats(rvc),
    }


def measure_answer(repeats: int) -> dict:
    from core.jake_core import JakeCore

    core = JakeCore()
    core.private_mode = True  # nessuna frase del benchmark in memoria/cronologia
    results = {}
    try:
        for prompt in CORE_PROMPTS:
            samples = []
            for _ in range(repeats):
                started = time.perf_counter()
                core.answer(prompt)
                samples.append((time.perf_counter() - started) * 1000)
            results[prompt] = latency_stats(samples)
    finally:
        core.shutdown()
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--with-core", action="store_true", help="misura anche JakeCore.answer() (Ollama)")
    parser.add_argument("--no-character", action="store_true", help="solo voce base, senza RVC")
    args = parser.parse_args(argv)

    provider, manager, config = build_provider(use_character=not args.no_character)
    timeline = Timeline()
    pipeline = instrument(provider, timeline)
    report: dict = {"pipeline": pipeline, "repeats": args.repeats,
                    "configured_voice_character": config.get("voice_character") or None}

    if manager is not None:
        started = time.perf_counter()
        ready = manager.ensure_running()
        report["rvc_server_start_ms"] = round((time.perf_counter() - started) * 1000, 1)
        if not ready:
            raise SystemExit("Server RVC non avviabile: la misura della voce di produzione non e' possibile")
        started = time.perf_counter()
        provider._prewarm_pipeline()
        report["rvc_prewarm_ms"] = round((time.perf_counter() - started) * 1000, 1)

    report["simple_response"] = measure_tts(provider, timeline, SIMPLE_RESPONSE, args.repeats)
    report["long_response"] = measure_tts(provider, timeline, LONG_RESPONSE, args.repeats)
    if args.with_core:
        report["answer"] = measure_answer(args.repeats)

    path = save_report("bench_voice_pipeline", report)
    print(f"Pipeline: {pipeline}")
    for key in ("simple_response", "long_response"):
        section = report[key]
        print(f"{key}: prima emissione p95={section['first_audio'].get('p95_ms')} ms, "
              f"pausa tra chunk p95={section['gap_between_chunks'].get('p95_ms')} ms, "
              f"TTS base p50={section['base_tts_per_chunk'].get('p50_ms')} ms, "
              f"RVC p50={section['rvc_per_chunk'].get('p50_ms')} ms")
    if "answer" in report:
        for prompt, stats in report["answer"].items():
            print(f"answer({prompt!r}): p50={stats.get('p50_ms')} ms p95={stats.get('p95_ms')} ms")
    print(f"Report: {path}")
    if manager is not None:
        manager.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
