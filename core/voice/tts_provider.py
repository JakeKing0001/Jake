import re
import threading
from abc import ABC, abstractmethod


class TtsProvider(ABC):
    """Interfaccia per i motori di sintesi vocale (text-to-speech)."""

    @abstractmethod
    def speak(self, text: str) -> None:
        """Sintetizza e riproduce il testo. Bloccante finché non finisce o viene interrotto."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Interrompe immediatamente la riproduzione in corso, se presente."""
        raise NotImplementedError


class Pyttsx3TtsProvider(TtsProvider):
    """Sintesi vocale offline via SAPI5 (Windows), attraverso pyttsx3."""

    def __init__(self, rate: int = 175, voice_id: str = None, preferred_gender: str = "male"):
        import pyttsx3

        self._pyttsx3 = pyttsx3
        self.rate = rate
        self.preferred_gender = preferred_gender
        self.matched_preferred_gender = False
        self.voice_id = voice_id or self._select_voice_id(preferred_gender)
        self._lock = threading.Lock()
        self._engine = None

    def _select_voice_id(self, preferred_gender: str) -> str | None:
        """Sceglie la migliore voce italiana disponibile, preferendo il genere richiesto."""
        probe = self._pyttsx3.init()
        try:
            voices = probe.getProperty("voices")
            italian_voices = [voice for voice in voices if self._is_italian(voice)]
            candidates = italian_voices or voices
            if not candidates:
                return None

            for voice in candidates:
                if self._matches_gender(voice, preferred_gender):
                    self.matched_preferred_gender = True
                    return voice.id

            return candidates[0].id if italian_voices else None
        finally:
            probe.stop()

    @staticmethod
    def _is_italian(voice) -> bool:
        languages = getattr(voice, "languages", None) or []
        if any("it-it" in str(language).lower().replace("_", "-") for language in languages):
            return True
        haystack = f"{voice.id} {voice.name}".lower()
        return "it-it" in haystack or "italian" in haystack or "italiano" in haystack

    @staticmethod
    def _matches_gender(voice, gender: str) -> bool:
        # "male" e' una sottostringa di "female": serve un confronto esatto (o a parola intera),
        # non un semplice "in", altrimenti una voce femminile risulterebbe sempre maschile.
        voice_gender = (getattr(voice, "gender", None) or "").strip().lower()
        if voice_gender:
            return voice_gender == gender.lower()
        haystack = f"{voice.id} {voice.name}".lower()
        return re.search(rf"\b{re.escape(gender.lower())}\b", haystack) is not None

    def _build_engine(self):
        engine = self._pyttsx3.init()
        engine.setProperty("rate", self.rate)
        if self.voice_id:
            engine.setProperty("voice", self.voice_id)
        return engine

    def speak(self, text: str) -> None:
        with self._lock:
            self._engine = self._build_engine()
            try:
                self._engine.say(text)
                self._engine.runAndWait()
            finally:
                self._engine = None

    def stop(self) -> None:
        engine = self._engine
        if engine is not None:
            engine.stop()

    def synthesize_to_file(self, text: str, output_path: str) -> None:
        """Salva la sintesi su file senza riprodurla (utile per verifiche senza altoparlanti)."""
        engine = self._build_engine()
        try:
            engine.save_to_file(text, output_path)
            engine.runAndWait()
        finally:
            engine.stop()
