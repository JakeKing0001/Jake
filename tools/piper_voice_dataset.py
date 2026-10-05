"""Dataset per addestrare una voce Piper con il timbro del personaggio (punto 11 del piano RAM/VRAM, 05/10/2026).

Oggi la voce di Jake e' edge-tts (DiegoNeural) convertita da RVC: torch resta caricato (~1 GB di VRAM, ~2 GB di RAM)
per tutta la sessione vocale. Una voce Piper addestrata sulle uscite di QUELLA pipeline gira in ONNX sulla CPU, in tempo
reale, senza torch. Questo strumento produce le coppie testo/audio:

  1. frasi italiane varie generate dal modello locale (nessuna cifra/emoji: espeak e edge le leggerebbero diverse),
     deduplicate, 3-28 parole;
  2. ogni frase passa per edge-tts e per il server RVC del personaggio, esattamente come quando Jake parla;
  3. `wavs/NNNNN.wav` + `metadata.csv` (formato `file|testo` di Piper).

Il consenso alla clonazione del timbro (core/voice/voice_consent.py) e' obbligatorio come a runtime. Se edge-tts non
risponde la frase si salta: il ripiego OneCore metterebbe nel dataset una voce diversa. Riprende da dove si era fermato.

  python -m tools.piper_voice_dataset --character jake_the_dog --sentences 1500 --out data/piper/jake_the_dog
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

TOPICS = [
    "meteo e stagioni", "cucina e ricette", "programmazione e computer", "scuola ed esami", "viaggi in treno",
    "sport e allenamento", "musica e concerti", "film e serie tv", "salute e sonno", "animali domestici",
    "scienza e spazio", "storia italiana", "lavoro e riunioni", "promemoria e appuntamenti", "file e cartelle",
    "videogiochi", "amicizia e famiglia", "spesa al supermercato", "tecnologia e smartphone", "natura e montagna",
    "mare ed estate", "libri e lettura", "risparmio e soldi", "traffico e automobili", "feste e compleanni",
    "un assistente vocale che risponde gentilmente", "un assistente che conferma un'azione eseguita",
    "un assistente che chiede una conferma", "un assistente che segnala un errore", "domande curiose",
    "emozioni e stati d'animo", "consigli pratici per la casa", "arte e musei", "lingue straniere", "matematica",
    "notizie del giorno", "una giornata tipo", "battute scherzose e simpatiche", "istruzioni passo passo",
    "esclamazioni di sorpresa", "domande con risposta breve", "frasi lunghe con molte virgole",
]
ALLOWED = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'’ ,.;:!?\-«»\"()]+$")


def clean_sentence(raw: str) -> str | None:
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip().strip('"').replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    words = text.split()
    if not 3 <= len(words) <= 28 or not ALLOWED.match(text) or not text[-1:] in ".!?":
        return None
    return text


def generate_sentences(target: int, model: str, existing: list[str], seed: int = 7) -> list[str]:
    from core.ollama_client import OllamaClient

    client = OllamaClient(timeout=180)
    rng = random.Random(seed)
    seen = {s.lower() for s in existing}
    sentences = list(existing)
    attempts = 0
    while len(sentences) < target and attempts < target:
        attempts += 1
        topic = rng.choice(TOPICS)
        style = rng.choice(["brevi", "di media lunghezza", "lunghe", "colloquiali", "formali", "con una domanda"])
        prompt = (f"Scrivi 20 frasi italiane {style} e diverse fra loro sul tema: {topic}. Una frase per riga, senza "
                  "numerazione, senza cifre (scrivi i numeri in lettere), senza emoji, senza virgolette.")
        text = client.chat_text(model, [{"role": "user", "content": prompt}], options={"temperature": 0.9}) or ""
        for line in text.splitlines():
            sentence = clean_sentence(line)
            if sentence and sentence.lower() not in seen:
                seen.add(sentence.lower())
                sentences.append(sentence)
        print(f"  frasi: {len(sentences)}/{target}", end="\r", flush=True)
    print()
    return sentences[:target]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--character", default="jake_the_dog")
    parser.add_argument("--sentences", type=int, default=1500)
    parser.add_argument("--out", default="data/piper/jake_the_dog")
    parser.add_argument("--model", default=None, help="modello per le frasi (default: ollama_model)")
    args = parser.parse_args(argv)

    from core.config import Config
    from core.voice.voice_consent import VoiceConsentRegistry

    if not VoiceConsentRegistry().is_allowed(args.character):
        print(f"Manca il consenso alla clonazione vocale per '{args.character}': niente dataset.")
        return 2
    config = Config()
    out = Path(args.out)
    (out / "wavs").mkdir(parents=True, exist_ok=True)
    sentences_path = out / "sentences.json"
    existing = json.loads(sentences_path.read_text(encoding="utf-8")) if sentences_path.exists() else []
    if len(existing) < args.sentences:
        print(f"Genero frasi con {args.model or config.get('ollama_model')}...")
        existing = generate_sentences(args.sentences, args.model or config.get("ollama_model", "qwen2.5:7b"), existing)
        sentences_path.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")

    from core.voice.edge_tts_provider import SAMPLE_RATE, EdgeTtsProvider
    from core.voice.rvc_client import RvcError
    from core.voice.rvc_server_manager import RvcServerManager

    tts = EdgeTtsProvider(voice=config.get("tts_voice") or "it-IT-DiegoNeural", rate=config.get("tts_rate") or "+8%")
    manager = RvcServerManager(model_name=args.character)
    if not manager.ensure_running(timeout=180):
        print("Server RVC non avviato.")
        return 3
    client = manager.client
    client.timeout = 120
    metadata = out / "metadata.csv"
    done = {}
    if metadata.exists():
        for line in metadata.read_text(encoding="utf-8").splitlines():
            name, _, text = line.partition("|")
            done[name] = text
    skipped = 0
    try:
        with metadata.open("a", encoding="utf-8") as meta:
            for index, sentence in enumerate(existing):
                name = f"{index:05d}.wav"
                if name in done:
                    continue
                mp3 = tts._synthesize(sentence)
                if not mp3:
                    skipped += 1
                    continue
                pcm = tts._decode_mp3(mp3)
                import io
                import wave

                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(SAMPLE_RATE)
                    wav_file.writeframes(pcm.tobytes())
                converted = None
                for _attempt in range(2):   # GPU contesa: un timeout di RVC non deve fermare ore di lavoro
                    try:
                        converted = client.convert(buffer.getvalue())
                        break
                    except RvcError:
                        manager.ensure_running(timeout=180)
                if converted is None:
                    skipped += 1
                    continue
                (out / "wavs" / name).write_bytes(converted)
                meta.write(f"{name}|{sentence}\n")
                meta.flush()
                print(f"  audio: {index + 1}/{len(existing)} (saltate {skipped})", end="\r", flush=True)
    finally:
        manager.stop()
        tts.close()
    print(f"\nFatto: {out / 'metadata.csv'} ({sum(1 for _ in metadata.open(encoding='utf-8'))} righe, saltate {skipped}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
