import re
import threading
import time

from core.logger import get_logger
from core.voice.audio_profile import apply_to_provider
from core.voice.listening_state import (
    EchoGuard, ListeningState, ListeningStateMachine, MicIndicator, RepeatGuard, WakeCooldown,
)
from core.voice.speech_text import STYLES, prepare_for_speech
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
                 wake_words=None, on_state=None, on_level=None, follow_up_seconds: float = None,
                 replay_window_seconds: float = 0.0, speech_style: str = "normal",
                 output_device_name: str | None = None):
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
        # F2.5.4/F2.5.7: stile del parlato (normal/brief/detailed/whisper/night) e nome del dispositivo di
        # uscita per regolare volume e ritmo. Uno stile sconosciuto ricade su "normal".
        self.speech_style = STYLES.get(speech_style, STYLES["normal"])
        self.output_device_name = output_device_name
        self._tts_thread = None
        self._running = False
        self._logger = get_logger()
        self.state = "idle"
        # F2.3.3: gli stati di ascolto vivono in una macchina esplicita (core/voice/listening_state.py); gli
        # attributi storici (paused_until, dictation_active...) restano come alias qui sotto.
        self.listening = ListeningStateMachine(
            command_wait_s=self.COMMAND_WAIT_SECONDS, follow_up_s=self.follow_up_seconds,
            confirmation_wait_s=self.CONFIRMATION_WAIT_SECONDS,
        )
        # F2.3.4: protezioni da eco (la voce di Jake ripresa dal microfono) e da attivazioni ripetute. Il
        # controllo anti-replay e' SPENTO di default (finestra 0): una persona che ripete "Jake che ore
        # sono" dopo pochi secondi non deve essere scambiata per una TV; si attiva con
        # `voice_replay_guard_seconds` in config solo dove serve.
        self.echo_guard = EchoGuard()
        self.wake_cooldown = WakeCooldown()
        self.repeat_guard = RepeatGuard(window_s=replay_window_seconds) if replay_window_seconds > 0 else None
        self.mic_indicator = self._build_mic_indicator()
        self._lock = threading.Lock()
        self._attach_hooks()

    # ---- alias storici degli stati di ascolto (F2.3.3) -------------------------------

    @property
    def paused_until(self) -> float:
        return self.listening.sleep_until

    @paused_until.setter
    def paused_until(self, value: float) -> None:
        self.listening.sleep_until = value

    @property
    def dictation_active(self) -> bool:
        return self.listening.dictating

    @dictation_active.setter
    def dictation_active(self, value: bool) -> None:
        self.listening.dictating = value

    @property
    def _follow_up_until(self) -> float:
        return self.listening.follow_up_until

    @_follow_up_until.setter
    def _follow_up_until(self, value: float) -> None:
        self.listening.follow_up_until = value

    @property
    def _awaiting_command_until(self) -> float:
        return self.listening.command_until

    @_awaiting_command_until.setter
    def _awaiting_command_until(self, value: float) -> None:
        self.listening.command_until = value

    def _open_follow_up(self) -> None:
        self.listening.follow_up_s = self.follow_up_seconds  # rispecchia un eventuale cambio a sessione avviata
        self.listening.open_follow_up()

    # ---- indicatore del microfono (F2.3.5) --------------------------------------------

    def _build_mic_indicator(self) -> MicIndicator | None:
        bus = getattr(self.jake_core, "event_bus", None)
        publish = getattr(bus, "publish", None)
        return MicIndicator(publish) if callable(publish) else None

    def _update_mic(self, open: bool, reason: str, discarding: bool = False) -> None:
        if self.mic_indicator is None:
            return
        try:
            self.mic_indicator.update(open, reason, discarding)
        except Exception:
            self._logger.exception("Errore pubblicando lo stato del microfono")

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
        system_advisor = getattr(self.jake_core, "system_advisor", None)
        if system_advisor is not None:
            system_advisor.on_advisory = self._on_advisory

    def _on_reminder_due(self, reminder: dict) -> None:
        # Passa dalla stessa modalita' di notifica (v4.3) del percorso CLI: vedi JakeCore.notify.
        message = self.jake_core.notify("reminder", self.jake_core.format_due_reminder(reminder))
        if message is None:
            return
        self._set_state("notify", message)
        self.speak(message)

    def _on_advisory(self, message: str) -> None:
        message = self.jake_core.notify("advisory", message)
        if message is None:
            return
        self._set_state("notify", message)
        self.speak(message)

    def _on_trigger_fired(self, trigger: dict, outcome, total_steps: int) -> None:
        from core.response_formatter import format_plan_outcome
        summary = format_plan_outcome(outcome, total_steps, self.jake_core.skill_registry)
        raw = f"Ho eseguito automaticamente {trigger.get('name')}. {summary.splitlines()[0]}"
        message = self.jake_core.notify("trigger", raw)
        if message is None:
            return
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
        # F2.5.1: Markdown e codice non si leggono; lo stile puo' abbreviare. Cio' che Jake dice davvero (non
        # il testo originale) e' cio' che l'eco-guard deve riconoscere.
        text = prepare_for_speech(text, self.speech_style) if text else ""
        if not text:
            return
        self._interrupt_speech()
        apply_to_provider(self.tts_provider, self.speech_style, self.output_device_name)

        def run():
            self.vad_listener.muted = True
            # F2.3.4: la voce di Jake ripresa dal microfono non deve diventare un comando.
            self.echo_guard.note_spoken(text)
            # F2.3.5: lo stream resta aperto mentre Jake parla, i frame si scartano soltanto.
            self._update_mic(True, "speaking", discarding=True)
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
                    self._open_follow_up()
                self._update_mic(True, self.listening.state.value)
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
        self.listening.sleep(max(1, int(minutes)))
        self._set_state("paused", f"{minutes} min")
        self._update_mic(True, ListeningState.SLEEP.value)

    def resume_listening(self) -> None:
        self.listening.wake_up()
        self._set_state("idle", "")
        self._update_mic(True, self.listening.state.value)

    def arm_listening(self) -> None:
        """Come aver detto 'Jake': la prossima frase e' un comando (click sull'orb dell'HUD)."""
        self._interrupt_speech()
        self.listening.arm_command()
        self._set_state("listening", "")

    def start_dictation(self) -> None:
        self.listening.start_dictation()
        self._set_state("dictation", "")
        self._update_mic(True, ListeningState.DICTATION.value)

    def stop_dictation(self) -> None:
        self.listening.stop_dictation()
        self._set_state("idle", "")
        self._update_mic(True, self.listening.state.value)

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
        self._update_mic(True, self.listening.state.value)
        try:
            self._listen_loop()
        finally:
            self._update_mic(False, "stopped")

    def _listen_loop(self) -> None:
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

        # F2.3.4: una frase che e' (quasi) tutta cio' che Jake ha appena detto e' il suo stesso eco; una frase
        # identica ripetuta a ridosso (se il controllo e' attivo) e' un loop, non una persona.
        if self.echo_guard.is_echo(text):
            self._logger.info("Ignorata: eco della voce di Jake")
            if self.state in ("transcribing", "listening"):
                self._set_state("idle", "")
            return
        if self.repeat_guard is not None and self.repeat_guard.is_replay(text):
            self._logger.info("Ignorata: ripetizione identica ravvicinata")
            return

        # In pausa: si sveglia solo con "Jake, svegliati" (o simili). La wake word puo' stare
        # ovunque nella frase (v4.1, Voice Natural 2.0: "scusa se ti disturbo, Jake, svegliati"
        # e' un risveglio naturale quanto "Jake, svegliati"), non solo all'inizio come richiede
        # _match_wake_word (che serve a isolare il comando che segue, qui non necessario: basta
        # sapere che l'utente ha detto il nome E una frase di risveglio nella stessa frase).
        if now < self.paused_until:
            if self._contains_wake_word_anywhere(text, self._is_close_to_wake_word) and WAKE_UP_PATTERN.search(text.lower()):
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
            if not self.wake_cooldown.allow():
                self._logger.info("Ignorata: attivazione entro il cooldown")
                return
            self.wake_cooldown.register()
            self._interrupt_speech()  # "Jake" detto mentre stava ancora parlando: interrompilo
            if not remainder:
                self.listening.arm_command()
                self._set_state("listening", "")
                return
            self._process_command(remainder)
            return

        if speaking:
            return  # probabilmente la voce di Jake stessa o rumore: ignora senza wake word

        self.listening.set_pending_action(bool(self.jake_core.conversation_state.has_pending_action()))
        if self.listening.accepts_without_wake_word():
            self._process_command(text)
            return

        # Frase senza wake word fuori da ogni finestra: ignorata.
        if self.state in ("transcribing", "listening"):
            self._set_state("idle", "")

    def _process_command(self, command: str) -> None:
        self.listening.command_consumed()
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
            self._open_follow_up()
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
