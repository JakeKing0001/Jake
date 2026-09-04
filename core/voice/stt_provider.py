from abc import ABC, abstractmethod


class SttProvider(ABC):
    """Interfaccia per i motori di riconoscimento vocale (speech-to-text)."""

    @abstractmethod
    def transcribe(self, audio, sample_rate: int) -> str:
        """Trascrive un array audio mono float32 (o un percorso file) nel testo riconosciuto."""
        raise NotImplementedError


class WhisperSttProvider(SttProvider):
    """Riconoscimento vocale offline via faster-whisper (nessuna chiamata di rete a runtime)."""

    def __init__(self, model_size: str = "small", language: str = "it", device: str = "cpu", compute_type: str = "int8"):
        from faster_whisper import WhisperModel

        self.language = language
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio, sample_rate: int = 16000) -> str:
        segments, _info = self._model.transcribe(audio, language=self.language)
        return " ".join(segment.text.strip() for segment in segments).strip()
