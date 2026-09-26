import math
import os
import site
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from core.logger import get_logger


class SttProvider(ABC):
    """Interfaccia per i motori di riconoscimento vocale (speech-to-text)."""

    @abstractmethod
    def transcribe(self, audio, sample_rate: int) -> str:
        """Trascrive un array audio mono float32 (o un percorso file) nel testo riconosciuto."""
        raise NotImplementedError


def _add_cuda_dll_dirs() -> None:
    """Le librerie CUDA/cuDNN installate via pip (nvidia-cublas-cu12, nvidia-cudnn-cu12) vivono
    dentro site-packages/nvidia/*/bin: su Windows vanno aggiunte esplicitamente al percorso di
    ricerca delle DLL, altrimenti ctranslate2 non le trova e ripiega sulla CPU in silenzio."""
    if sys.platform != "win32":
        return
    roots = []
    for directory in site.getsitepackages() + [site.getusersitepackages()]:
        nvidia_dir = Path(directory) / "nvidia"
        if nvidia_dir.is_dir():
            roots.append(nvidia_dir)
    for root in roots:
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            bin_dir = root / sub / "bin"
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except (OSError, AttributeError):
                    pass
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def cuda_available() -> bool:
    _add_cuda_dll_dirs()
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class WhisperSttProvider(SttProvider):
    """Riconoscimento vocale offline via faster-whisper (nessuna chiamata di rete a runtime).

    v3.0: usa la GPU quando c'e' (large-v3-turbo in float16: piu' preciso di 'medium' e 5-10
    volte piu' veloce di 'medium' su CPU), altrimenti 'medium' int8 su CPU come prima. Il
    prompt iniziale ("hotwords") e' costruito dinamicamente con i nomi delle app installate e
    il vocabolario di Jake, cosi' Whisper riconosce "Blender", "Visual Studio Code", "Opera"
    invece di storpiarli in parole italiane a caso."""

    GPU_MODEL = "large-v3-turbo"
    CPU_MODEL = "medium"
    BASE_VOCABULARY = [
        "Jake", "apri", "chiudi", "cerca su YouTube", "cerca su Google", "metti un timer",
        "promemoria", "che ore sono", "scrivi", "clicca su", "volume", "luminosità",
        "Visual Studio Code", "Spotify", "Opera", "Blender", "Discord", "WhatsApp", "Chrome",
    ]
    MAX_PROMPT_CHARS = 600  # initial_prompt: max ~224 token

    def __init__(self, model_size: str = None, language: str = "it", device: str = None,
                 compute_type: str = None, hotwords: list[str] = None):
        from faster_whisper import WhisperModel

        logger = get_logger()
        self.language = language
        use_cuda = device == "cuda" or (device is None and cuda_available())
        self.device = "cuda" if use_cuda else "cpu"
        self.model_size = model_size or (self.GPU_MODEL if use_cuda else self.CPU_MODEL)
        self.compute_type = compute_type or ("float16" if use_cuda else "int8")
        try:
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        except Exception:
            if not use_cuda:
                raise
            # GPU presente ma qualcosa non va (driver, VRAM piena): meglio la CPU che niente.
            logger.exception("Whisper su GPU non disponibile, ripiego sulla CPU")
            self.device, self.model_size, self.compute_type = "cpu", model_size or self.CPU_MODEL, "int8"
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        logger.info("Whisper pronto: modello %s su %s (%s)", self.model_size, self.device, self.compute_type)

        self.hotwords = list(hotwords or [])
        self.initial_prompt = self._build_prompt()

    def set_hotwords(self, hotwords: list[str]) -> None:
        self.hotwords = list(hotwords or [])
        self.initial_prompt = self._build_prompt()

    def _build_prompt(self) -> str:
        seen = set()
        words = []
        for word in self.BASE_VOCABULARY + self.hotwords:
            key = word.strip().lower()
            if key and key not in seen and len(key) <= 40:
                seen.add(key)
                words.append(word.strip())
        prompt = ", ".join(words)
        return prompt[: self.MAX_PROMPT_CHARS].rsplit(",", 1)[0] + "." if len(prompt) > self.MAX_PROMPT_CHARS else prompt + "."

    def transcribe(self, audio, sample_rate: int = 16000) -> str:
        return self.transcribe_detailed(audio, sample_rate)[0]

    def transcribe_detailed(self, audio, sample_rate: int = 16000) -> tuple[str, float | None]:
        """Come transcribe(), piu' la confidenza (F2.2.2): exp della media di avg_logprob dei
        segmenti pesata sulla durata, in [0, 1]. None se il modello non la riporta (o se le
        allucinazioni note hanno azzerato il testo: una confidenza su un testo scartato non ha
        senso). E' la probabilita' media per token secondo Whisper, non una probabilita' che il
        testo sia corretto: serve a confrontare frasi tra loro, non come garanzia."""
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            initial_prompt=self.initial_prompt,
            beam_size=5,
            # Senza condition_on_previous_text=False un'allucinazione su una frase (es. rumore
            # scambiato per "Sottotitoli e revisione a cura di QTSS") tende a ripetersi anche
            # sulle frasi successive perche' Whisper la riusa come contesto.
            condition_on_previous_text=False,
            # Secondo filtro VAD (Silero) interno a Whisper, oltre a webrtcvad in vad_listener.py:
            # scarta i tratti senza voce residui nel segmento invece di trascriverli a caso.
            vad_filter=True,
            no_speech_threshold=0.6,
        )
        segments = list(segments)
        text = self._drop_hallucinations(" ".join(segment.text.strip() for segment in segments).strip())
        return text, (self._confidence(segments) if text else None)

    def transcribe_partial(self, audio, sample_rate: int = 16000) -> tuple[str, float | None]:
        """Decodifica LEGGERA per i sottotitoli in tempo reale (F2.2.2): greedy, senza secondo VAD
        Silero e senza timestamp. Il gate hardware del 26/09/2026 ha misurato partial da 1-5 s con la
        decodifica completa (beam 5 + VAD) usata anche qui: il testo definitivo resta quello della
        trascrizione finale, un partial deve solo arrivare in tempo."""
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            initial_prompt=self.initial_prompt,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
            no_speech_threshold=0.6,
        )
        segments = list(segments)
        text = self._drop_hallucinations(" ".join(segment.text.strip() for segment in segments).strip())
        return text, (self._confidence(segments) if text else None)

    @staticmethod
    def _confidence(segments) -> float | None:
        weighted, total = 0.0, 0.0
        for segment in segments:
            logprob = getattr(segment, "avg_logprob", None)
            if isinstance(logprob, bool) or not isinstance(logprob, (int, float)):
                continue
            duration = getattr(segment, "end", 0.0) - getattr(segment, "start", 0.0)
            weight = duration if isinstance(duration, (int, float)) and duration > 0 else 1.0
            weighted += logprob * weight
            total += weight
        if total == 0:
            return None
        return round(min(1.0, max(0.0, math.exp(weighted / total))), 4)

    _HALLUCINATIONS = (
        "sottotitoli e revisione a cura di", "sottotitoli creati dalla comunita'", "sottotitoli creati dalla comunità",
        "grazie per aver guardato", "iscriviti al canale", "amara.org", "qtss",
    )

    def _drop_hallucinations(self, text: str) -> str:
        lowered = text.lower()
        if any(marker in lowered for marker in self._HALLUCINATIONS):
            return ""
        return text
