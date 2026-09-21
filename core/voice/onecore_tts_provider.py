import asyncio
import io
import wave

from core.voice.tts_provider import TtsProvider, scale_pcm


class OneCoreTtsProvider(TtsProvider):
    """Sintesi vocale offline via le voci "OneCore" di Windows (Impostazioni > Ora e lingua > Voce).

    pyttsx3/SAPI5 vede solo le voci "Desktop" classiche: su molti PC (incluso questo) le voci
    aggiuntive, spesso maschili, sono registrate solo come OneCore e restano invisibili a
    pyttsx3. Questo provider le usa direttamente tramite l'API WinRT (pacchetto 'winsdk')."""

    def __init__(self, voice_name_contains: str = None, preferred_gender: str = "male"):
        from winsdk.windows.media.speechsynthesis import SpeechSynthesizer, VoiceGender

        self._SpeechSynthesizer = SpeechSynthesizer
        self._VoiceGender = VoiceGender
        self.preferred_gender = preferred_gender
        self.matched_preferred_gender = False
        self.voice = self._select_voice(voice_name_contains, preferred_gender)
        self._playing = False
        self.volume = 1.0

    def set_speech_params(self, volume: float = 1.0, rate_delta_percent: int = 0) -> bool:
        """Solo il volume (guadagno sul PCM): il ritmo della voce OneCore non e' regolato qui."""
        self.volume = min(1.0, max(0.0, volume))
        return True

    def _select_voice(self, voice_name_contains, preferred_gender):
        voices = list(self._SpeechSynthesizer.all_voices)
        if not voices:
            return None

        if voice_name_contains:
            for voice in voices:
                if voice_name_contains.lower() in voice.display_name.lower():
                    self.matched_preferred_gender = True
                    return voice

        italian_voices = [voice for voice in voices if voice.language.lower().startswith("it")]
        candidates = italian_voices or voices
        target_gender = self._VoiceGender.MALE if preferred_gender == "male" else self._VoiceGender.FEMALE
        for voice in candidates:
            if voice.gender == target_gender:
                self.matched_preferred_gender = True
                return voice

        return candidates[0]

    def speak(self, text: str) -> None:
        if self.voice is None:
            return
        audio_bytes = asyncio.run(self._synthesize(text))
        self._play(audio_bytes)

    async def _synthesize(self, text: str) -> bytes:
        from winsdk.windows.storage.streams import DataReader

        synth = self._SpeechSynthesizer()
        synth.voice = self.voice
        stream = await synth.synthesize_text_to_stream_async(text)
        reader = DataReader(stream)
        await reader.load_async(stream.size)
        data = bytearray(stream.size)
        reader.read_bytes(data)
        return bytes(data)

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
        if self._playing:
            import sounddevice as sd
            sd.stop()

    def synthesize_to_file(self, text: str, output_path: str) -> None:
        """Salva la sintesi su file senza riprodurla (utile per verifiche senza altoparlanti)."""
        audio_bytes = asyncio.run(self._synthesize(text))
        with open(output_path, "wb") as f:
            f.write(audio_bytes)
