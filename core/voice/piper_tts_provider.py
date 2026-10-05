"""Voce del personaggio con Piper: ONNX sulla CPU, in tempo reale, senza torch ne' server RVC.

La voce di Jake era edge-tts (online) convertita da RVC: torch restava caricato (~1 GB di VRAM, ~2 GB di RAM) per tutta
la sessione vocale e ogni frase faceva due passaggi (rete + conversione). Una voce Piper addestrata sulle uscite di
quella stessa pipeline (tools/piper_voice_dataset.py, training/piper/) produce il timbro direttamente, anche offline.

Il modello sta in `data/piper/models/<personaggio>.onnx` (+ `.onnx.json`). Il consenso alla clonazione del timbro vale
come per RVC: se viene revocato si parla con la voce di base."""
from __future__ import annotations

import queue
import threading
import wave
from pathlib import Path

from core.logger import get_logger
from core.voice.tts_provider import TtsProvider, scale_pcm

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "piper" / "models"


def piper_model_path(character: str, models_dir: Path = MODELS_DIR) -> Path | None:
    path = models_dir / f"{character}.onnx"
    return path if path.exists() and path.with_suffix(".onnx.json").exists() else None


class PiperTtsProvider(TtsProvider):
    def __init__(self, model_path: str | Path, base_tts_provider: TtsProvider | None = None, consent_check=None,
                 rate_percent: int = 0):
        from piper import PiperVoice

        self.voice = PiperVoice.load(str(model_path))
        self.sample_rate = int(self.voice.config.sample_rate)
        self.base_tts_provider = base_tts_provider
        self.consent_check = consent_check
        self._base_rate_percent = rate_percent
        self.rate_percent = rate_percent
        self.volume = 1.0
        self.matched_preferred_gender = True
        self._interrupted = False
        self._playing = False
        self._lock = threading.Lock()
        self._logger = get_logger()

    def prewarm(self) -> None:
        """La prima sintesi costa ~2,8 s (inizializzazione di ONNX Runtime ed espeak, misura del 05/10/2026): la si
        fa all'avvio, in background, invece che sulla prima risposta."""
        def warm() -> None:
            try:
                for _ in self.synthesize_chunks("Pronto."):
                    pass
            except Exception:
                self._logger.exception("Riscaldamento Piper fallito")

        threading.Thread(target=warm, name="jake-piper-prewarm", daemon=True).start()

    def set_speech_params(self, volume: float = 1.0, rate_delta_percent: int = 0) -> bool:
        self.volume = min(1.0, max(0.0, volume))
        self.rate_percent = self._base_rate_percent + rate_delta_percent
        if self.base_tts_provider is not None:
            self.base_tts_provider.set_speech_params(volume, rate_delta_percent)
        return True

    def _config(self):
        from piper import SynthesisConfig

        # ritmo piu' alto = durata dei fonemi piu' breve
        return SynthesisConfig(length_scale=1.0 / max(0.5, 1.0 + self.rate_percent / 100.0))

    def synthesize_chunks(self, text: str):
        """Un array int16 per frase, nell'ordine: Piper divide il testo da se'."""
        for chunk in self.voice.synthesize(text, syn_config=self._config()):
            yield chunk.audio_int16_array

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self.consent_check is not None and not self.consent_check():
            if self.base_tts_provider is not None:
                self.base_tts_provider.speak(text)
            return
        with self._lock:
            self._interrupted = False
            # la frase successiva si sintetizza mentre suona quella corrente
            chunks: queue.Queue = queue.Queue(maxsize=2)

            def produce() -> None:
                try:
                    for pcm in self.synthesize_chunks(text):
                        if self._interrupted:
                            break
                        chunks.put(pcm)
                except Exception:
                    self._logger.exception("Sintesi Piper fallita")
                finally:
                    chunks.put(None)

            threading.Thread(target=produce, name="jake-piper-tts", daemon=True).start()
            while True:
                pcm = chunks.get()
                if pcm is None or self._interrupted:
                    break
                self._play(pcm)

    def _play(self, pcm) -> None:
        import sounddevice as sd

        if pcm.size == 0 or self._interrupted:
            return
        self._playing = True
        try:
            audible = scale_pcm(pcm, self.volume)
            self._emit_reference(audible, self.sample_rate)
            sd.play(audible, samplerate=self.sample_rate)
            sd.wait()
        finally:
            self._playing = False

    def stop(self) -> None:
        self._interrupted = True
        if self._playing:
            try:
                import sounddevice as sd

                sd.stop()
            except Exception:
                pass
        if self.base_tts_provider is not None:
            self.base_tts_provider.stop()

    def synthesize_to_file(self, text: str, output_path: str) -> None:
        import numpy as np

        parts = list(self.synthesize_chunks(text))
        pcm = np.concatenate(parts) if parts else np.zeros((0,), dtype=np.int16)
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm.astype(np.int16).tobytes())

    def close(self) -> None:
        self.stop()
        close_base = getattr(self.base_tts_provider, "close", None)
        if callable(close_base):
            close_base()
