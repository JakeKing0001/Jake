import io
import os
import tempfile
import wave

from core.voice.rvc_client import RvcError
from core.voice.tts_provider import TtsProvider, scale_pcm


class CharacterTtsProvider(TtsProvider):
    """Sintetizza con una voce di base (es. Cosimo) e converte il timbro con un modello RVC
    (es. "Jake il Cane"), riproducendo il risultato. Se il server RVC non e' raggiungibile,
    ripiega sulla voce di base invece di restare muto."""

    def __init__(self, base_tts_provider, server_manager, consent_check=None):
        """`consent_check` (F2.5.6): callable() -> bool, richiamata a OGNI frase. Se torna False la
        conversione del timbro NON avviene e si parla con la voce di base: una revoca del consenso
        vale subito, anche a sessione in corso. None = nessun controllo (solo per i test)."""
        self.base_tts_provider = base_tts_provider
        self.server_manager = server_manager
        self.consent_check = consent_check
        self.volume = 1.0
        self.matched_preferred_gender = getattr(base_tts_provider, "matched_preferred_gender", True)
        self._playing = False
        # La conversione RVC richiede alcuni secondi PRIMA che inizi la riproduzione: senza
        # questo flag, un'interruzione arrivata durante quella finestra non avrebbe alcun
        # effetto (stop() agiva solo a riproduzione gia' avviata) e Jake parlerebbe comunque.
        self._interrupted = False

    def speak(self, text: str) -> None:
        self._interrupted = False
        if self.consent_check is not None and not self.consent_check():
            self.base_tts_provider.speak(text)
            return
        if not self.server_manager.ensure_running():
            self.base_tts_provider.speak(text)
            return

        source_bytes = self._synthesize_to_bytes(text)
        if self._interrupted:
            return

        try:
            converted_bytes = self.server_manager.client.convert(source_bytes)
        except RvcError:
            self.base_tts_provider.speak(text)
            return
        if self._interrupted:
            return

        self._play(converted_bytes)

    def set_speech_params(self, volume: float = 1.0, rate_delta_percent: int = 0) -> bool:
        self.volume = min(1.0, max(0.0, volume))
        self.base_tts_provider.set_speech_params(volume, rate_delta_percent)
        return True

    def _synthesize_to_bytes(self, text: str) -> bytes:
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            self.base_tts_provider.synthesize_to_file(text, tmp_path)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            os.unlink(tmp_path)

    def _play(self, audio_bytes: bytes) -> None:
        import numpy as np
        import sounddevice as sd

        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            frames = wav_file.readframes(wav_file.getnframes())
            sample_width = wav_file.getsampwidth()
            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()

        dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(sample_width, np.int16)
        samples = np.frombuffer(frames, dtype=dtype)
        if channels > 1:
            samples = samples.reshape(-1, channels)

        self._playing = True
        try:
            audible = scale_pcm(samples, self.volume)
            self._emit_reference(audible, sample_rate)
            sd.play(audible, samplerate=sample_rate)
            sd.wait()
        finally:
            self._playing = False

    def stop(self) -> None:
        self._interrupted = True
        if self._playing:
            import sounddevice as sd
            sd.stop()
        self.base_tts_provider.stop()
