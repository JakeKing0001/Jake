"""Voce neurale (v3.0) via Microsoft Edge TTS: qualita' da assistente vero (it-IT-Diego,
Giuseppe, Elsa, Isabella) invece delle voci SAPI di Windows. Richiede internet: quando
manca, o il servizio non risponde entro pochi secondi, ripiega sulla voce offline
(OneCore/pyttsx3) invece di restare muto.

Per non far aspettare l'utente sulle risposte lunghe, il testo viene spezzato in frasi:
mentre suona la prima, la seconda e' gia' in sintesi (prefetch su un thread)."""
import asyncio
import io
import re
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

from core.logger import get_logger
from core.network import is_online
from core.voice.tts_provider import TtsProvider

DEFAULT_VOICE = "it-IT-DiegoNeural"
SAMPLE_RATE = 24000


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;:])\s+|\n+", text.strip())
    sentences = [part.strip() for part in parts if part and part.strip()]
    # Frammenti troppo corti (es. "Ok.") vengono uniti al successivo: ogni chiamata di rete
    # costa ~0.4s, non vale la pena farne una per due sillabe.
    merged = []
    for sentence in sentences:
        if merged and len(merged[-1]) < 25:
            merged[-1] = merged[-1] + " " + sentence
        else:
            merged.append(sentence)
    return merged or ([text.strip()] if text.strip() else [])


class EdgeTtsProvider(TtsProvider):
    def __init__(self, voice: str = DEFAULT_VOICE, rate: str = "+8%", pitch: str = "+0Hz",
                 fallback: TtsProvider = None, timeout: float = 6.0):
        import edge_tts  # noqa: F401  (ImportError se manca il pacchetto)

        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.fallback = fallback
        self.timeout = timeout
        self.matched_preferred_gender = True
        self._interrupted = False
        self._playing = False
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._logger = get_logger()
        self._consecutive_failures = 0

    # ---- sintesi ---------------------------------------------------------------------

    async def _synthesize_async(self, text: str) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        return buffer.getvalue()

    def _synthesize(self, text: str) -> bytes | None:
        try:
            return asyncio.run(asyncio.wait_for(self._synthesize_async(text), timeout=self.timeout))
        except Exception as exc:
            self._logger.warning("Edge TTS non disponibile (%s): uso la voce offline", type(exc).__name__)
            return None

    @staticmethod
    def _decode_mp3(mp3_bytes: bytes):
        """mp3 -> PCM int16 mono 24 kHz via PyAV (gia' presente come dipendenza di faster-whisper)."""
        import av
        import numpy as np

        container = av.open(io.BytesIO(mp3_bytes))
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        frames = []
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                frames.append(resampled.to_ndarray())
        for resampled in resampler.resample(None):
            frames.append(resampled.to_ndarray())
        container.close()
        if not frames:
            return np.zeros((0,), dtype=np.int16)
        return np.concatenate(frames, axis=1).reshape(-1).astype(np.int16)

    # ---- riproduzione ----------------------------------------------------------------

    def _play(self, pcm) -> None:
        import sounddevice as sd

        if pcm.size == 0 or self._interrupted:
            return
        self._playing = True
        try:
            sd.play(pcm, samplerate=SAMPLE_RATE)
            sd.wait()
        finally:
            self._playing = False

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._interrupted = False
            if not is_online():
                self._speak_fallback(text)
                return

            sentences = _split_sentences(text)
            future = self._executor.submit(self._synthesize, sentences[0])
            for index, sentence in enumerate(sentences):
                mp3 = future.result()
                if index + 1 < len(sentences):
                    future = self._executor.submit(self._synthesize, sentences[index + 1])
                if self._interrupted:
                    return
                if mp3 is None:
                    self._consecutive_failures += 1
                    # Il resto della risposta con la voce offline, senza perdere il filo.
                    self._speak_fallback(" ".join(sentences[index:]))
                    return
                self._consecutive_failures = 0
                try:
                    pcm = self._decode_mp3(mp3)
                except Exception:
                    self._logger.exception("Errore decodificando l'audio Edge TTS")
                    self._speak_fallback(" ".join(sentences[index:]))
                    return
                self._play(pcm)
                if self._interrupted:
                    return

    def _speak_fallback(self, text: str) -> None:
        if self.fallback is not None and not self._interrupted:
            self.fallback.speak(text)

    def stop(self) -> None:
        self._interrupted = True
        if self._playing:
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
        if self.fallback is not None:
            self.fallback.stop()

    def synthesize_to_file(self, text: str, output_path: str) -> None:
        """WAV su disco (per la conversione RVC o per test senza altoparlanti)."""
        mp3 = self._synthesize(text) if is_online() else None
        if mp3 is None:
            if self.fallback is not None and hasattr(self.fallback, "synthesize_to_file"):
                self.fallback.synthesize_to_file(text, output_path)
            return
        pcm = self._decode_mp3(mp3)
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm.tobytes())
