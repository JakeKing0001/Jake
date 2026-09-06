import re
import threading
import time

from core.logger import get_logger
from core.voice.vad_listener import VadListener

# Varianti di riferimento: Whisper a volte trascrive male "Jake" (nome poco comune in italiano).
# Il confronto vero e proprio (_is_close_to_wake_word) usa la distanza di edit da queste, cosi'
# anche varianti non elencate qui esplicitamente (es. "jeic", "gek") vengono comunque accettate.
WAKE_WORD_VARIANTS = {"jake", "geek", "jack", "jache", "jek", "gec", "jeik", "cheic"}
WAKE_WORD_MAX_DISTANCE = 1

STOP_DICTATION_PATTERN = re.compile(r"\b(fine|stop|basta|termina|chiudi)\s+(la\s+)?dettatura\b|\bsmetti di scrivere\b|\bbasta dettare\b")
WAKE_UP_PATTERN = re.compile(r"\b(svegliati|riprendi|torna|ci sei|ascolta)\b")


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    previous_row = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current_row = [i]
        for j, char_b in enumerate(b, start=1):
            current_row.append(min(
                current_row[j - 1] + 1,  # inserimento
                previous_row[j] + 1,  # cancellazione
                previous_row[j - 1] + (char_a != char_b),  # sostituzione
            ))
        previous_row = current_row
    return previous_row[-1]


class WakeWordSession:
    """Ascolto continuo con parola di attivazione "Jake" (v2.0), esteso nella v3.0 con:
    - stati osservabili (on_state) e livello del microfono (on_level) per l'HUD;
    - finestra di follow-up: dopo una risposta, per qualche secondo si puo' continuare a
      parlare senza ripetere "Jake"; una domanda di conferma ("confermi?") accetta il si'/no
      senza wake word;
    - dettatura: tutto cio' che viene detto viene digitato nel campo attivo;
    - pausa ("non ascoltare per 10 minuti"), interruzione della voce, mute del microfono
      mentre Jake parla (per non trascrivere la propria voce).

    Usa lo stesso motore Whisper sia per la wake word sia per il comando: ogni frase
    rilevata dal VAD viene trascritta localmente e scartata subito se non contiene "Jake"."""

    COMMAND_WAIT_SECONDS = 8.0
    FOLLOW_UP_SECONDS = 6.0
    CONFIRMATION_WAIT_SECONDS = 20.0

    def __init__(self, jake_core, stt_provider, tts_provider, vad_listener: VadListener = None,
                 wake_words=None, on_state=None, on_level=None, follow_up_seconds: float = None):
        self.jake_core = jake_core
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider
        self.vad_listener = vad_listener or VadListener(on_level=self._on_frame_level)
        if self.vad_listener.on_level is None:
            self.vad_listener.on_level = self._on_frame_level
        self.wake_words = wake_words or WAKE_WORD_VARIANTS
        self.on_state = on_state  # callable(state: str, detail: str)
        self.on_level = on_level  # callable(level: float, is_speech: bool)
        self.follow_up_seconds = self.FOLLOW_UP_SECONDS if follow_up_seconds is None else follow_up_seconds
        self._tts_thread = None
        self._running = False
        self._logger = get_logger()
        self.state = "idle"
        self.dictation_active = False
        self.paused_until = 0.0
        self._follow_up_until = 0.0
        self._awaiting_command_until = 0.0
        self._lock = threading.Lock()
        self._attach_hooks()

    # ---- integrazione col core --------------------------------------------------------

    def _attach_hooks(self) -> None:
        hooks = getattr(self.jake_core, "session_hooks", None)
        if hooks is None:
            return
        hooks.kind = "voice"
        hooks.stop_speaking = self._interrupt_speech
        hooks.pause_listening = self.pause_listening
        hooks.resume_listening = self.resume_listening
        hooks.start_dictation = self.start_dictation
        hooks.stop_dictation = self.stop_dictation
        hooks.speak = self.speak
        hooks.set_state = self._set_state
        scheduler = getattr(self.jake_core, "scheduler", None)
        if scheduler is not None:
            scheduler.on_due = self._on_reminder_due
        trigger_scheduler = getattr(self.jake_core, "trigger_scheduler", None)
        if trigger_scheduler is not None:
            trigger_scheduler.on_trigger = self._on_trigger_fired

    def _on_reminder_due(self, reminder: dict) -> None:
        message = self.jake_core.format_due_reminder(reminder)
        self._set_state("notify", message)
        self.speak(message)

    def _on_trigger_fired(self, trigger: dict, outcome, total_steps: int) -> None:
        from core.response_formatter import format_plan_outcome
        summary = format_plan_outcome(outcome, total_steps, self.jake_core.skill_registry)
        message = f"Ho eseguito automaticamente {trigger.get('name')}. {summary.splitlines()[0]}"
        self._set_state("notify", message)
        self.speak(message)

    # ---- stato ------------------------------------------------------------------------

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        if self.on_state is not None:
            try:
                self.on_state(state, detail)
            except Exception:
                self._logger.exception("Errore nel callback di stato")

    def _on_frame_level(self, level: float, is_speech: bool) -> None:
        if self.on_level is not None:
            self.on_level(level, is_speech)

    # ---- voce -------------------------------------------------------------------------

    def speak(self, text: str) -> None:
        self._speak_async(text)

    def _speak_async(self, text: str) -> None:
        if not text:
            return
        self._interrupt_speech()

        def run():
            self.vad_listener.muted = True
            self._set_state("speaking", text)
            try:
                self.tts_provider.speak(text)
            except Exception:
                self._logger.exception("Errore nella sintesi vocale")
            finally:
                # Piccolo margine: la coda dell'audio puo' ancora rimbombare nel microfono.
                time.sleep(0.25)
                self.vad_listener.muted = False
                if self.follow_up_seconds > 0:
                    self._follow_up_until = time.time() + self.follow_up_seconds
                if self.state == "speaking":
                    self._set_state("dictation" if self.dictation_active else "idle", "")

        self._tts_thread = threading.Thread(target=run, daemon=True)
        self._tts_thread.start()

    def _interrupt_speech(self) -> None:
        if self._tts_thread is not None and self._tts_thread.is_alive():
            try:
                self.tts_provider.stop()
            except Exception:
                pass
            self._tts_thread.join(timeout=2)
        self.vad_listener.muted = False

    # ---- controlli --------------------------------------------------------------------

    def pause_listening(self, minutes: int = 10) -> None:
        self.paused_until = time.time() + max(1, int(minutes)) * 60
        self._set_state("paused", f"{minutes} min")

    def resume_listening(self) -> None:
        self.paused_until = 0.0
        self._set_state("idle", "")

    def start_dictation(self) -> None:
        self.dictation_active = True
        self._set_state("dictation", "")

    def stop_dictation(self) -> None:
        self.dictation_active = False
        self._set_state("idle", "")

    # ---- wake word --------------------------------------------------------------------

    def _is_close_to_wake_word(self, word: str) -> bool:
        return any(_edit_distance(word, variant) <= WAKE_WORD_MAX_DISTANCE for variant in self.wake_words)

    def _match_wake_word(self, text: str):
        """Se la frase inizia con la wake word, ritorna il resto del testo (puo' essere vuoto
        se l'utente ha detto solo "Jake"); altrimenti None. Tollera "ehi Jake" / "ok Jake"."""
        normalized = text.strip().lower()
        words = re.findall(r"\w+", normalized, flags=re.UNICODE)
        if not words:
            return None
        skip = 0
        if words[0] in ("ehi", "hey", "ok", "okay", "ciao", "senti", "ei") and len(words) > 1:
            skip = 1
        if not self._is_close_to_wake_word(words[skip]):
            return None
        # "Jake, Jake, apri..." (Whisper a volte raddoppia il nome): salta tutte le ripetizioni.
        end = skip + 1
        while end < len(words) and self._is_close_to_wake_word(words[end]):
            end += 1
        pattern = r"\W*\w+\W*" * end
        match = re.match(pattern, normalized, flags=re.UNICODE)
        return normalized[match.end():].strip() if match else ""

    @staticmethod
    def _contains_wake_word_anywhere(text: str, matcher) -> bool:
        return any(matcher(word) for word in re.findall(r"\w+", text.lower()))

    # ---- ciclo principale -------------------------------------------------------------

    def run(self) -> None:
        self._running = True
        print('Jake e\' in ascolto continuo. Di\' "Jake" per attivarlo, Ctrl+C per uscire.')
        if not self.vad_listener.is_available():
            print("Nessun microfono disponibile: la voce continua non puo' funzionare su questo computer.")
            self._set_state("error", "Nessun microfono disponibile")
            return
        self._set_state("idle", "")

        for utterance in self.vad_listener.listen_for_utterances(lambda: self._running):
            if utterance.size == 0:
                continue
            try:
                self._handle_utterance(utterance)
            except Exception:
                # Un errore imprevisto qui non deve terminare l'ascolto continuo, altrimenti Jake
                # resta muto in silenzio finche' l'utente non si accorge e riavvia a mano.
                self._logger.exception("Errore gestendo una frase")
                self._set_state("idle", "")
            if not self._running:
                break

    def _transcribe(self, utterance) -> str:
        self._set_state("transcribing", "")
        try:
            return self.stt_provider.transcribe(utterance, self.vad_listener.SAMPLE_RATE).strip()
        except Exception:
            self._logger.exception("Errore nella trascrizione vocale")
            return ""

    def _handle_utterance(self, utterance) -> None:
        now = time.time()
        speaking = self._tts_thread is not None and self._tts_thread.is_alive()

        text = self._transcribe(utterance)
        if not text:
            if self.state == "transcribing":
                self._set_state("listening" if now < self._awaiting_command_until else "idle", "")
            return
        self._logger.info("Sentito: %s", text)

        # In pausa: si sveglia solo con "Jake, svegliati" (o simili).
        if now < self.paused_until:
            remainder = self._match_wake_word(text)
            if remainder is not None and WAKE_UP_PATTERN.search(remainder):
                self.resume_listening()
                self._respond("Eccomi, ti ascolto di nuovo.")
            else:
                self._set_state("paused", "")
            return

        # Dettatura: tutto viene scritto, tranne il comando di uscita.
        if self.dictation_active:
            if STOP_DICTATION_PATTERN.search(text.lower()):
                self.stop_dictation()
                self._respond("Dettatura terminata.")
                return
            remainder = self._match_wake_word(text)
            if remainder is not None and STOP_DICTATION_PATTERN.search(remainder):
                self.stop_dictation()
                self._respond("Dettatura terminata.")
                return
            self._type_dictation(text)
            self._set_state("dictation", text)
            return

        remainder = self._match_wake_word(text)
        if remainder is not None:
            self._interrupt_speech()  # "Jake" detto mentre stava ancora parlando: interrompilo
            if not remainder:
                self._awaiting_command_until = time.time() + self.COMMAND_WAIT_SECONDS
                self._set_state("listening", "")
                return
            self._process_command(remainder)
            return

        if speaking:
            return  # probabilmente la voce di Jake stessa o rumore: ignora senza wake word

        pending = self.jake_core.conversation_state.has_pending_action()
        if now < self._awaiting_command_until or now < self._follow_up_until or (pending and now < self._follow_up_until + self.CONFIRMATION_WAIT_SECONDS):
            self._process_command(text)
            return

        # Frase senza wake word fuori da ogni finestra: ignorata.
        if self.state in ("transcribing", "listening"):
            self._set_state("idle", "")

    def _process_command(self, command: str) -> None:
        self._awaiting_command_until = 0.0
        self._follow_up_until = 0.0
        print(f"Tu > {command}")
        self._set_state("thinking", command)
        response = self.jake_core.answer(command)
        print(f"Jake > {response}")

        if response == self.jake_core.EXIT_SENTINEL:
            print("Chiusura...")
            self._set_state("exit", "")
            self._running = False
            return
        self._respond(response)

    def _respond(self, response: str) -> None:
        if not response:
            self._follow_up_until = time.time() + self.follow_up_seconds
            self._set_state("dictation" if self.dictation_active else "idle", "")
            return
        self._set_state("responding", response)
        try:
            self._speak_async(response)
        except Exception:
            self._logger.exception("Errore avviando la sintesi vocale")

    @staticmethod
    def _type_dictation(text: str) -> None:
        import keyboard

        spoken_punctuation = {
            "punto e virgola": ";", "punto interrogativo": "?", "punto esclamativo": "!",
            "due punti": ":", "virgola": ",", "punto": ".", "a capo": "\n", "nuova riga": "\n",
            "aperta parentesi": "(", "chiusa parentesi": ")",
        }
        lowered = text.strip()
        for spoken, symbol in spoken_punctuation.items():
            lowered = re.sub(rf"\s*\b{re.escape(spoken)}\b\s*", symbol + (" " if symbol not in "(\n" else ""), lowered, flags=re.IGNORECASE)
        keyboard.write(lowered + " ", delay=0.004)

    def stop(self) -> None:
        self._running = False
        self._interrupt_speech()
