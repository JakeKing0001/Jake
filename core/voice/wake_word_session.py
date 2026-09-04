import re
import threading

from core.voice.vad_listener import VadListener

# Varianti tollerate: Whisper a volte trascrive male "Jake" (nome poco comune in italiano).
WAKE_WORD_VARIANTS = {"jake", "geek", "jack", "jache", "jek"}


class WakeWordSession:
    """Ascolto continuo con parola di attivazione "Jake" (v2.0): niente push-to-talk, Jake
    ascolta sempre e si attiva quando senti dire il suo nome.

    Usa lo stesso motore Whisper (WhisperSttProvider) sia per riconoscere la parola di
    attivazione sia per il comando vero e proprio: ogni frase rilevata dal VAD viene
    trascritta localmente e scartata subito se non contiene "Jake". Nulla lascia il PC,
    ma a differenza di un vero motore di wake-word dedicato (piu' leggero, es. openWakeWord)
    ogni frase pronunciata in stanza viene comunque trascritta in locale per il controllo."""

    def __init__(self, jake_core, stt_provider, tts_provider, vad_listener: VadListener = None, wake_words=None):
        self.jake_core = jake_core
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider
        self.vad_listener = vad_listener or VadListener()
        self.wake_words = wake_words or WAKE_WORD_VARIANTS
        self._tts_thread = None
        self._running = False

    def _speak_async(self, text: str) -> None:
        self._interrupt_speech()
        self._tts_thread = threading.Thread(target=self.tts_provider.speak, args=(text,), daemon=True)
        self._tts_thread.start()

    def _interrupt_speech(self) -> None:
        if self._tts_thread is not None and self._tts_thread.is_alive():
            self.tts_provider.stop()
            self._tts_thread.join(timeout=2)

    def _match_wake_word(self, text: str):
        """Se la frase inizia con la wake word, ritorna il resto del testo (puo' essere vuoto
        se l'utente ha detto solo "Jake"); altrimenti None."""
        normalized = text.strip().lower()
        words = re.findall(r"\w+", normalized, flags=re.UNICODE)
        if not words or words[0] not in self.wake_words:
            return None

        match = re.match(r"\W*\w+\W*", normalized, flags=re.UNICODE)
        return normalized[match.end():].strip() if match else ""

    def run(self) -> None:
        self._running = True
        print('Jake e\' in ascolto continuo. Di\' "Jake" per attivarlo, Ctrl+C per uscire.')
        if not self.vad_listener.is_available():
            print("Nessun microfono disponibile: la voce continua non puo' funzionare su questo computer.")
            return

        awaiting_command = False
        for utterance in self.vad_listener.listen_for_utterances(lambda: self._running):
            if utterance.size == 0:
                continue

            text = self.stt_provider.transcribe(utterance, self.vad_listener.SAMPLE_RATE).strip()
            if not text:
                continue

            if awaiting_command:
                command = text
                awaiting_command = False
            else:
                remainder = self._match_wake_word(text)
                if remainder is None:
                    continue
                self._interrupt_speech()  # "Jake" detto mentre stava ancora parlando: interrompilo
                if not remainder:
                    print("(attivato, in ascolto del comando...)")
                    awaiting_command = True
                    continue
                command = remainder

            print(f"Tu > {command}")
            response = self.jake_core.answer(command.lower())
            print(f"Jake > {response}")

            if response == self.jake_core.EXIT_SENTINEL:
                print("Chiusura...")
                self._running = False
                return

            self._speak_async(response)

    def stop(self) -> None:
        self._running = False
