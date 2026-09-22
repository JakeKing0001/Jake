import threading

from core.logger import get_logger
from core.request_context import reset_current_speaker_profile_id, set_current_speaker_profile_id
from core.voice.microphone import Microphone, MicrophoneError
from core.voice.speaker_profile import SpeakerProfileStore, extract_features, identify
from core.voice.speech_text import prepare_for_speech


class PushToTalkSession:
    """Ciclo vocale: tasto premuto -> registra -> trascrivi -> rispondi -> parla.

    Il tasto di push-to-talk interrompe subito Jake se sta ancora parlando (la sintesi gira
    in un thread separato) prima di iniziare una nuova registrazione: e' cosi' che si ottiene
    l'interruzione della voce, senza dover aspettare la fine della risposta precedente.
    """

    def __init__(self, jake_core, stt_provider, tts_provider, microphone: Microphone = None, hotkey: str = "f9",
                 speaker_store: SpeakerProfileStore | None = None):
        self.jake_core = jake_core
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider
        self.microphone = microphone or Microphone()
        self.hotkey = hotkey
        self._tts_thread = None
        # F2.7 (adozione, stesso identico principio di core/voice/wake_word_session.py): None (il
        # default) preserva il comportamento di sempre - vedi il docstring di
        # current_speaker_profile_id in core/request_context.py.
        self.speaker_store = speaker_store

    def _speak_async(self, text: str) -> None:
        text = prepare_for_speech(text)  # F2.5.1: niente Markdown/codice letti a voce
        if not text:
            return
        self._interrupt_speech()
        self._tts_thread = threading.Thread(target=self.tts_provider.speak, args=(text,), daemon=True)
        self._tts_thread.start()

    def _interrupt_speech(self) -> None:
        if self._tts_thread is not None and self._tts_thread.is_alive():
            self.tts_provider.stop()
            self._tts_thread.join(timeout=2)

    def _identify_speaker_token(self, audio):
        """F2.7 (adozione, stesso identico principio/stessa cautela di
        core/voice/wake_word_session.py::WakeWordSession._identify_speaker_token): None (il caso
        normale) senza uno speaker_store o con una confidenza che non e' "high" - un
        riconoscimento incerto non deve mai etichettare il turno con un profilo indovinato. Un
        errore qui non deve mai impedire a Jake di rispondere."""
        if self.speaker_store is None:
            return None
        try:
            features = extract_features(audio)
            hint = identify(features, self.speaker_store.profiles())
        except Exception:
            get_logger().exception("Errore identificando la voce")
            return None
        if hint.confidence != "high" or hint.profile_id is None:
            return None
        return set_current_speaker_profile_id(hint.profile_id)

    def run(self) -> None:
        import keyboard

        print(f"Jake (voce) avviato. Tieni premuto [{self.hotkey}] per parlare, Ctrl+C per uscire.")
        if not self.microphone.is_available():
            print("Nessun microfono disponibile: la modalità voce non può funzionare su questo computer.")
            return

        while True:
            keyboard.wait(self.hotkey)
            self._interrupt_speech()

            print("In ascolto...")
            try:
                audio = self.microphone.record_while(lambda: keyboard.is_pressed(self.hotkey))
            except MicrophoneError as exc:
                print(f"Errore microfono: {exc}")
                continue

            if audio.size == 0:
                continue

            # La trascrizione e la sintesi vocale girano fuori dal try/except di
            # JakeCore.answer(): un errore imprevisto qui (es. Whisper, audio device) non deve
            # terminare l'intera sessione vocale, altrimenti Jake resta muto in silenzio finche'
            # l'utente non si accorge e riavvia a mano.
            try:
                text = self.stt_provider.transcribe(audio, self.microphone.sample_rate).strip()
            except Exception:
                get_logger().exception("Errore nella trascrizione vocale")
                print("Non sono riuscito a capire, riprova.")
                continue
            if not text:
                continue
            print(f"Tu > {text}")

            speaker_token = self._identify_speaker_token(audio)
            try:
                response = self.jake_core.answer(text.lower())
            finally:
                if speaker_token is not None:
                    reset_current_speaker_profile_id(speaker_token)
            print(f"Jake > {response}")

            if response == self.jake_core.EXIT_SENTINEL:
                print("Chiusura...")
                return

            try:
                self._speak_async(response)
            except Exception:
                get_logger().exception("Errore avviando la sintesi vocale")
