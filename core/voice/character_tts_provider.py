import io
import os
import tempfile
import wave
import threading
from concurrent.futures import Future
from core.voice.daemon_executor import DaemonExecutor
from core.voice.speech_text import split_prosodic
from core.voice.rvc_client import RvcError
from core.voice.tts_provider import TtsProvider, scale_pcm


class CharacterTtsProvider(TtsProvider):
    """Sintetizza con una voce di base (es. Cosimo) e converte il timbro con un modello RVC
    (es. "Jake il Cane"), riproducendo il risultato. Se il server RVC non e' raggiungibile,
    ripiega sulla voce di base invece di restare muto."""

    FIRST_CHUNK_MAX_CHARS = 75
    MAX_CHUNK_CHARS = 150
    MIN_CHUNK_CHARS = 45
    # Frasi brevi gia' convertite: le conferme fisse ("Va bene, annullo.") partono subito invece
    # di aspettare sintesi + RVC (~1 s) con il microfono in mute. Solo frasi corte, poche voci.
    CACHE_MAX_CHARS = 60
    CACHE_MAX_ENTRIES = 32
    PREWARM_PHRASES = ("Va bene, annullo.", "Eccomi, ti ascolto di nuovo.", "Dettatura terminata.")

    @classmethod
    def _speech_chunks(cls, text: str) -> list[str]:
        chunks = split_prosodic(
            text,
            max_chars=cls.MAX_CHUNK_CHARS,
            min_chars=cls.MIN_CHUNK_CHARS,
        )

        if not chunks:
            return []

        first = chunks[0]

        if len(first) <= cls.FIRST_CHUNK_MAX_CHARS:
            return chunks

        first_parts = split_prosodic(
            first,
            max_chars=cls.FIRST_CHUNK_MAX_CHARS,
            min_chars=20,
        )

        return first_parts + chunks[1:]

    def _begin_generation(self) -> int:
        with self._state_lock:
            self._generation += 1
            self._interrupted = False
            return self._generation


    def _is_current(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._generation and not self._interrupted

    def _cache_key(self, text: str) -> tuple:
        base = self.base_tts_provider
        return (text, getattr(base, "voice", None), getattr(base, "rate", None))

    def _prepare_chunk(self, text: str, generation: int,) -> bytes | None:
        key = self._cache_key(text)
        with self._state_lock:
            cached = self._converted_cache.get(key)
        if cached is not None:
            return cached if self._is_current(generation) else None
        source_bytes = self._synthesize_to_bytes(text)
        # Stop arrivato mentre il TTS base stava sintetizzando:
        # non iniziare nemmeno la conversione RVC.
        if not self._is_current(generation):
            return None

        converted_bytes = self.server_manager.client.convert(source_bytes)

        if len(text) <= self.CACHE_MAX_CHARS:
            with self._state_lock:
                if len(self._converted_cache) >= self.CACHE_MAX_ENTRIES:
                    self._converted_cache.pop(next(iter(self._converted_cache)))
                self._converted_cache[key] = converted_bytes

        # Stop arrivato durante RVC: il risultato ormai prodotto
        # non deve essere riprodotto.
        if not self._is_current(generation):
            return None

        return converted_bytes

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
        # Due worker: una frase nuova (es. "Va bene, annullo.") non deve aspettare in coda la
        # sintesi/conversione ormai inutile della frase interrotta, che non si puo' abortire a
        # meta' ma il cui risultato viene comunque scartato (_is_current).
        # Thread daemon: una conversione abbandonata non trattiene l'uscita di Jake (vedi daemon_executor).
        self._executor = DaemonExecutor(
            max_workers=2,
            thread_name_prefix="jake-rvc-prefetch",
        )
        self._state_lock = threading.Lock()
        self._generation = 0
        self._prewarm_future = None
        self._converted_cache: dict[tuple, bytes] = {}

    def prewarm(self) -> None:
        """Scalda anche TTS + prima inferenza RVC, senza riprodurre audio."""
        if self._prewarm_future is not None and not self._prewarm_future.done():
            return

        self._prewarm_future = self._executor.submit(self._prewarm_pipeline)


    def _prewarm_pipeline(self) -> bool:
        if self.consent_check is not None and not self.consent_check():
            return False

        if not self.server_manager.ensure_running():
            return False

        try:
            source_bytes = self._synthesize_to_bytes("Ciao.")
            self.server_manager.client.convert(source_bytes)
            for phrase in self.PREWARM_PHRASES:
                self._prepare_chunk(phrase, self._generation_snapshot())
            return True
        except Exception:
            return False

    def _generation_snapshot(self) -> int:
        with self._state_lock:
            return self._generation

    def speak(self, text: str) -> None:
        generation = self._begin_generation()

        if self.consent_check is not None and not self.consent_check():
            self.base_tts_provider.speak(text)
            return

        if not self.server_manager.ensure_running():
            self.base_tts_provider.speak(text)
            return

        chunks = self._speech_chunks(text)

        if not chunks:
            return

        # Prepara il primo chunk.
        future = self._executor.submit(
            self._prepare_chunk,
            chunks[0],
            generation,
        )

        for index, _chunk in enumerate(chunks):
            try:
                converted_bytes = self._await_chunk(future, generation)
            except RvcError:
                if self._is_current(generation):
                    # I chunk precedenti sono già stati pronunciati:
                    # fallback soltanto sul resto.
                    remaining = " ".join(chunks[index:])
                    self.base_tts_provider.speak(remaining)
                return

            if converted_bytes is None:
                return
            
            if not self._is_current(generation):
                return

            # Punto fondamentale:
            # prepara il PROSSIMO pezzo PRIMA di iniziare a riprodurre
            # quello appena completato.
            next_future = None

            if index + 1 < len(chunks):
                next_future = self._executor.submit(
                    self._prepare_chunk,
                    chunks[index + 1],
                    generation,
                )

            self._play(converted_bytes)

            if not self._is_current(generation):
                return

            future = next_future

    POLL_SECONDS = 0.05

    def _await_chunk(self, future: Future, generation: int) -> bytes | None:
        """Attende il chunk preparato, ma smette appena arriva stop(): speak() deve tornare
        subito, altrimenti il thread vocale resta vivo (microfono in mute) finche' la
        conversione RVC in corso non finisce."""
        while True:
            try:
                return future.result(timeout=self.POLL_SECONDS)
            except TimeoutError:
                if not self._is_current(generation):
                    return None

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
        with self._state_lock:
            self._interrupted = True

            # Invalida anche eventuali conversioni ancora in background.
            self._generation += 1

        if self._playing:
            import sounddevice as sd
            sd.stop()

        self.base_tts_provider.stop()

    def close(self) -> None:
        """Fine sessione: stop, nessuna conversione in coda parte piu', la voce di base chiude le sue."""
        self.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        close_base = getattr(self.base_tts_provider, "close", None)
        if callable(close_base):
            close_base()
