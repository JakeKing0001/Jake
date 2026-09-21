"""Corpus audio per i benchmark voce (F2.1.1/F2.1.2/F2.1.4/F2.1.6).

Due famiglie di clip, tenute distinte di proposito:

- **sintetiche deterministiche** (`build_synthetic_corpus`): generate con numpy da un seed fisso,
  quindi identiche bit per bit su ogni macchina. Non vengono mai scritte nel repository: si
  rigenerano al volo, e il loro hash (`corpus_hash`) e' quanto basta per dimostrare che due
  report sono stati prodotti sullo stesso corpus. Nessuna voce di nessuno e' coinvolta.
- **sintesi vocale locale** (`render_tts_entries`): frasi reali dette da una voce SAPI installata,
  con il testo di riferimento per il WER. Dipendono dalle voci del PC, quindi NON entrano
  nell'hash stabile.

Le registrazioni consensuali di persone vere restano fuori dal repository (vedi
docs/voice-corpus.md): qui esistono solo il formato dei metadati e gli strumenti per misurarle
senza mai copiarne l'audio in un report - `manifest()` e i report contengono etichette, metriche e
hash, mai campioni audio (F2.1.4)."""
from __future__ import annotations

import hashlib
import shutil
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
DEFAULT_SEED = 20260921


@dataclass(frozen=True)
class Segment:
    label: str  # "speech" | "noise" | "silence"
    start_s: float
    end_s: float
    text: str | None = None


@dataclass
class CorpusEntry:
    id: str
    pcm: np.ndarray  # int16 mono, mai serializzato nei manifest
    segments: list[Segment]
    speaker: str = "none"
    distance_m: float = 0.5
    device: str = "synthetic"
    noise: str = "none"
    ground_truth_text: str | None = None
    deterministic: bool = True
    tags: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return round(len(self.pcm) / SAMPLE_RATE, 3)

    def expected_utterances(self, min_gap_s: float) -> int:
        """Quante frasi un segmentatore con `min_gap_s` di silenzio dovrebbe produrre: i segmenti
        di parlato separati da meno di min_gap_s contano come una sola frase."""
        speech = sorted((s for s in self.segments if s.label == "speech"), key=lambda s: s.start_s)
        count, last_end = 0, None
        for seg in speech:
            if last_end is None or seg.start_s - last_end >= min_gap_s:
                count += 1
            last_end = seg.end_s
        return count


# ---- generatori di segnale -------------------------------------------------------------------

def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.int16)


def _noise(seconds: float, std: float, rng: np.random.Generator) -> np.ndarray:
    return np.clip(rng.standard_normal(int(seconds * SAMPLE_RATE)) * std, -32768, 32767).astype(np.int16)


def _tone(seconds: float, freq: float, amplitude: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (np.sin(2 * np.pi * freq * t) * amplitude).astype(np.int16)


def _speechlike(seconds: float, rng: np.random.Generator, f0: float = 120.0, gain: float = 1.0) -> np.ndarray:
    """Segnale con struttura da voce (armoniche sotto inviluppo a tre formanti, modulazione sillabica
    a 4 Hz, vibrato di f0, un filo di rumore): NON e' parlato vero e non pretende di esserlo, serve
    a esercitare VAD e segmentatore con un'onda riproducibile. Il parlato vero sta nelle clip TTS."""
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.05 * np.sin(2 * np.pi * 3 * t))) / SAMPLE_RATE
    signal = np.zeros(n)
    for k in range(1, 40):
        freq = k * f0
        envelope = sum(np.exp(-(((freq - centre) / width) ** 2)) for centre, width in ((700, 150), (1200, 200), (2600, 300)))
        signal += envelope * np.sin(k * phase) / k**0.3
    signal *= 0.5 * (1 + np.sin(2 * np.pi * 4 * t)) ** 0.7
    signal += 0.02 * rng.standard_normal(n)
    signal /= np.max(np.abs(signal))
    return (signal * 0.5 * 32767 * gain).astype(np.int16)


def _compose(parts: list[tuple[str, np.ndarray]]) -> tuple[np.ndarray, list[Segment]]:
    chunks, segments, cursor = [], [], 0
    for label, audio in parts:
        start = cursor / SAMPLE_RATE
        cursor += len(audio)
        chunks.append(audio)
        segments.append(Segment(label, round(start, 3), round(cursor / SAMPLE_RATE, 3)))
    return np.concatenate(chunks), segments


def build_synthetic_corpus(seed: int = DEFAULT_SEED) -> list[CorpusEntry]:
    """Corpus deterministico: stesso seed, stessi byte. Copre le condizioni che F2.1.2 chiede di
    annotare (parlato, rumore, distanza, silenzio) con casi facili e casi che un VAD a energia
    sbaglia per costruzione (rumore forte a banda larga, tono continuo)."""
    rng = np.random.default_rng(seed)
    entries: list[CorpusEntry] = []

    def add(entry_id: str, parts, **meta) -> None:
        pcm, segments = _compose(parts)
        entries.append(CorpusEntry(entry_id, pcm, segments, **meta))

    add("silence_3s", [("silence", _silence(3.0))], tags=["negative"])
    add("quiet_room_3s", [("silence", _noise(3.0, 30, rng))], noise="room-30", tags=["negative"])
    add("loud_noise_2s", [("noise", _noise(2.0, 3000, rng))], noise="white-3000", tags=["negative", "hard-negative"])
    add("tone_2s", [("noise", _tone(2.0, 440, 10000))], noise="tone-440", tags=["negative", "hard-negative"])
    add(
        "one_utterance",
        [("silence", _silence(0.6)), ("speech", _speechlike(1.5, rng)), ("silence", _silence(1.0))],
        speaker="synthetic-f0-120", tags=["speech"],
    )
    add(
        "two_utterances_long_gap",
        [
            ("silence", _silence(0.5)), ("speech", _speechlike(1.2, rng)),
            ("silence", _silence(1.2)), ("speech", _speechlike(1.0, rng, f0=200)),
            ("silence", _silence(0.8)),
        ],
        speaker="synthetic-f0-120+200", tags=["speech", "multi"],
    )
    add(
        "two_bursts_short_gap",
        [
            ("silence", _silence(0.4)), ("speech", _speechlike(0.8, rng)),
            ("silence", _silence(0.25)), ("speech", _speechlike(0.8, rng)),
            ("silence", _silence(1.0)),
        ],
        speaker="synthetic-f0-120", tags=["speech", "pause-inside"],
    )
    # distanza: stessa struttura con ampiezza ridotta sopra un rumore di fondo, come una persona
    # lontana dal microfono (attenuazione simulata, non acustica di stanza). A 6 m il VAD a energia
    # perde tutto il parlato: e' un limite reale, tenuto nel corpus perche' resti visibile.
    for entry_id, distance, gain in (("far_speech_3m", 3.0, 0.03), ("very_far_speech_6m", 6.0, 0.01)):
        far = _speechlike(1.5, rng, gain=gain)
        mixed = _noise(3.1, 20, rng).astype(np.int32)
        offset = int(0.6 * SAMPLE_RATE)
        mixed[offset:offset + len(far)] += far
        pcm = np.clip(mixed, -32768, 32767).astype(np.int16)
        entries.append(CorpusEntry(
            entry_id, pcm,
            [Segment("silence", 0.0, 0.6), Segment("speech", 0.6, 2.1), Segment("silence", 2.1, round(len(pcm) / SAMPLE_RATE, 3))],
            speaker="synthetic-f0-120", distance_m=distance, noise="room-20", tags=["speech", "far"],
        ))
    return entries


# ---- hash, manifest, frame ---------------------------------------------------------------------

def entry_hash(entry: CorpusEntry) -> str:
    return hashlib.sha256(entry.pcm.astype("<i2").tobytes()).hexdigest()


def corpus_hash(entries: list[CorpusEntry]) -> str:
    """Hash dell'insieme delle sole clip deterministiche, in ordine di id: se cambia, due report
    NON sono confrontabili. Le clip TTS (dipendenti dalla voce del PC) restano fuori."""
    digest = hashlib.sha256()
    for entry in sorted((e for e in entries if e.deterministic), key=lambda e: e.id):
        digest.update(entry.id.encode())
        digest.update(entry_hash(entry).encode())
    return digest.hexdigest()


def manifest(entries: list[CorpusEntry]) -> dict:
    """Metadati e hash SENZA audio (F2.1.4): e' cio' che si puo' salvare in un report o condividere."""
    return {
        "sample_rate": SAMPLE_RATE,
        "corpus_hash": corpus_hash(entries),
        "entries": [
            {
                "id": e.id, "duration_s": e.duration_s, "sha256": entry_hash(e), "deterministic": e.deterministic,
                "speaker": e.speaker, "distance_m": e.distance_m, "device": e.device, "noise": e.noise,
                "ground_truth_text": e.ground_truth_text, "tags": e.tags,
                "segments": [
                    {"label": s.label, "start_s": s.start_s, "end_s": s.end_s, "text": s.text} for s in e.segments
                ],
            }
            for e in entries
        ],
    }


def frames(entry: CorpusEntry) -> list[np.ndarray]:
    """Frame da 30 ms come li vede VadListener; l'ultimo frame incompleto viene scartato, come fa lui."""
    pcm = entry.pcm
    return [pcm[i:i + FRAME_SAMPLES].reshape(-1, 1) for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES)]


def frame_truth(entry: CorpusEntry) -> list[bool]:
    """Verita' per frame: un frame e' 'parlato' se almeno meta' della sua durata cade in un
    segmento annotato 'speech'."""
    truth = []
    for index in range(len(frames(entry))):
        start = index * FRAME_MS / 1000
        end = start + FRAME_MS / 1000
        overlap = sum(max(0.0, min(end, s.end_s) - max(start, s.start_s)) for s in entry.segments if s.label == "speech")
        truth.append(overlap >= (end - start) / 2)
    return truth


# ---- sintesi vocale locale (facoltativa) -------------------------------------------------------

def render_tts_entries(phrases: list[str], workdir: Path, language_prefix: str = "it") -> list[CorpusEntry]:
    """Pronuncia ogni frase con una voce SAPI e la porta a 16 kHz mono int16. Ritorna [] se
    pyttsx3 o una voce della lingua richiesta non ci sono (es. runner CI): mai un errore, perche'
    queste clip sono un extra locale. I WAV temporanei finiscono in `workdir` e vengono cancellati
    subito dopo la lettura (F2.1.4: nessun audio resta su disco)."""
    try:
        import pyttsx3
        voices = [
            v for v in _new_engine(pyttsx3).getProperty("voices")
            if any(language_prefix in str(lang).lower() for lang in (v.languages or []))
        ]
    except Exception:
        return []
    if not voices:
        return []
    workdir.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, phrase in enumerate(phrases):
        path = workdir / f"tts_{index}.wav"
        try:
            # Un engine NUOVO per frase: pyttsx3 (SAPI5) tiene l'engine in cache e il secondo
            # runAndWait() sullo stesso oggetto si blocca per sempre (riprodotto qui: la prima
            # frase esce in 0,07 s, la seconda non termina mai). _new_engine svuota la cache.
            engine = _new_engine(pyttsx3)
            engine.setProperty("voice", voices[0].id)
            engine.save_to_file(phrase, str(path))
            engine.runAndWait()
            engine.stop()
            pcm = _read_wav_16k_mono(path)
        except Exception:
            continue
        finally:
            path.unlink(missing_ok=True)
        full = np.concatenate([_silence(0.4), pcm, _silence(0.8)])
        speech_end = round(0.4 + len(pcm) / SAMPLE_RATE, 3)
        entries.append(CorpusEntry(
            f"tts_{index}", full,
            [
                Segment("silence", 0.0, 0.4),
                Segment("speech", 0.4, speech_end, phrase),
                Segment("silence", speech_end, round(len(full) / SAMPLE_RATE, 3)),
            ],
            speaker=voices[0].name, device="sapi-tts", ground_truth_text=phrase, deterministic=False, tags=["speech", "tts"],
        ))
    return entries


def _new_engine(pyttsx3_module):
    pyttsx3_module._activeEngines.clear()
    return pyttsx3_module.init()


def _read_wav_16k_mono(path: Path) -> np.ndarray:
    with wave.open(str(path)) as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        if width != 2:
            raise ValueError(f"campioni a {width * 8} bit non supportati")
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        target = int(len(data) * SAMPLE_RATE / rate)
        data = np.interp(np.linspace(0, len(data) - 1, target), np.arange(len(data)), data)
    return np.clip(data, -32768, 32767).astype(np.int16)


# ---- cancellazione (F2.1.6) --------------------------------------------------------------------

def purge_corpus_dir(directory: Path) -> int:
    """Cancella una cartella di registrazioni locali e ritorna quanti file c'erano. Rifiuta cio'
    che non e' una cartella (mai cancellare "per sbaglio" un file singolo) e la radice di un
    disco o la home: e' distruttiva, pensata solo per un corpus dedicato."""
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"non e' una cartella: {directory}")
    if directory == Path(directory.anchor) or directory == Path.home().resolve():
        raise ValueError(f"rifiuto di cancellare {directory}: troppo ampia per essere un corpus")
    count = sum(1 for path in directory.rglob("*") if path.is_file())
    shutil.rmtree(directory)
    return count


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Strumenti per il corpus audio locale (vedi docs/voice-corpus.md).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("manifest", help="stampa il manifest (metadati + hash, mai audio) del corpus sintetico")
    purge = sub.add_parser("purge", help="cancella una cartella di registrazioni locali")
    purge.add_argument("directory", type=Path)
    args = parser.parse_args()

    if args.command == "manifest":
        import json

        print(json.dumps(manifest(build_synthetic_corpus()), ensure_ascii=False, indent=2))
    else:
        print(f"Cancellati {purge_corpus_dir(args.directory)} file da {args.directory}")


if __name__ == "__main__":
    main()
