import re
import threading
import time

import numpy as np

from core.logger import get_logger
from core.request_context import (
    reset_current_speaker_profile_id, reset_current_stt_confidence, set_current_speaker_profile_id,
    set_current_stt_confidence,
)
from core.voice.audio_profile import apply_to_provider, classify_output_device
from core.voice.barge_in import BargeInController, classify_interruption
from core.voice.live_transcriber import LiveTranscriber
from core.voice.listening_state import (
    EchoGuard, ListeningState, ListeningStateMachine, MicIndicator, RepeatGuard, WakeCooldown,
)
from core.voice.playback_aec import PlaybackAec
from core.voice.speaker_profile import SpeakerProfileStore, extract_features, identify
from core.voice.speech_text import STYLES, prepare_for_speech
from core.voice.streaming_stt import TranscriptEvent, to_hud_event
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


class _SessionSpeaker:
    """Vista della sessione come "parlato interrompibile" per `BargeInController`: `cancel()` ferma il
    motore TTS e sblocca il microfono; i contatori delle unita' non ci sono (il provider riceve il
    testo intero, non unita' singole: vedi il gradino 2 di integrazione F2 in ROADMAP_EXECUTION.md)."""

    units_spoken: list = []
    units_dropped = 0

    def __init__(self, session) -> None:
        self._session = session

    def cancel(self) -> int:
        self._session._interrupt_speech()
        return 0


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
                 output_device_name: str | None = None, barge_in: str = "off", partials: str = "off",
                 on_transcript=None, speaker_store: SpeakerProfileStore | None = None):
        self.jake_core = jake_core
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider
        # F2.7 (adozione, prima fetta - identificazione): None (il default) preserva il
        # comportamento di sempre, nessun profilo arruolato viene mai cercato. Quando presente,
        # SOLO un riconoscimento ad alta confidenza per la frase appena trascritta imposta
        # core.request_context.current_speaker_profile_id() per la durata della chiamata a
        # core.answer() - un'informazione di IDENTIFICAZIONE, mai un permesso (vedi
        # core/profiles.py): non sposta memoria ne' cronologia, quell'isolamento resta un
        # incremento successivo dichiarato (vedi il docstring del contextvar).
        self.speaker_store = speaker_store
        self._last_utterance_audio: np.ndarray | None = None
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
        # F2.4.3: barge-in. "off" (default): nessuna interruzione a voce, come prima. "auto": solo con cuffie
        # (l'eco e' trascurabile: nel benchmark simulato 4/4 interruzioni e nessun falso senza AEC). "on":
        # sempre, con l'AEC se il provider pubblica il riferimento audio; con altoparlanti e senza AEC
        # Jake si interromperebbe da solo (benchmark: 4/4 falsi barge-in), quindi "on" e' una scelta esplicita.
        self.barge_in_mode = barge_in if barge_in in ("off", "auto", "on") else "off"
        self.playback_aec = PlaybackAec()
        self.barge_in = BargeInController(_SessionSpeaker(self))
        self._interrupted_turn = None
        self._interrupted_deadline = 0.0
        self._attach_reference_sink()
        self.vad_listener.on_speaking_frame = self._on_speaking_frame
        # F2.2.2: trascrizione parziale (sottotitoli, HUD nativo, companion). Un solo modello non regge due
        # decodifiche insieme, quindi le serializza `_stt_lock` (condiviso con la trascrizione finale): un
        # partial in corso puo' ritardare la finale. Per questo "auto" li abilita solo su GPU, dove una
        # decodifica dura frazioni di secondo (su CPU medium int8 misurato: 4,2 s per una frase intera).
        self._stt_lock = threading.Lock()
        self.on_transcript = on_transcript  # callable(TranscriptEvent): partial E final, per un HUD che vuole i sottotitoli
        self.live_transcriber = None
        self._live_active = False
        self._live_ids = None
        if partials == "on" or (partials == "auto" and getattr(stt_provider, "device", None) == "cuda"):
            self.live_transcriber = LiveTranscriber(stt_provider, self._deliver_transcript, model_lock=self._stt_lock)
            self.vad_listener.on_utterance_frame = self._on_utterance_frame
            self.vad_listener.on_utterance_end = self._on_utterance_end
        self.last_confidence = None
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

    # ---- barge-in (F2.4.3-F2.4.5) ------------------------------------------------------

    def _attach_reference_sink(self) -> None:
        """Il provider TTS, se riproduce da se', pubblica cio' che manda agli altoparlanti (AEC)."""
        try:
            self.tts_provider.reference_sink = self.playback_aec.push_reference
        except Exception:
            self._logger.debug("Il provider TTS non accetta un reference_sink: nessuna AEC")

    def _barge_in_enabled(self) -> bool:
        if self.barge_in_mode == "on":
            return True
        if self.barge_in_mode == "auto":
            return classify_output_device(self.output_device_name) == "headphones"
        return False

    def _speaking(self) -> bool:
        return self._tts_thread is not None and self._tts_thread.is_alive()

    def _on_speaking_frame(self, frame, is_speech: bool, level: float) -> None:
        """Un frame di microfono arrivato MENTRE Jake parla (gira sul thread di ascolto)."""
        if not self._barge_in_enabled() or not self._speaking():
            return
        audio = np.asarray(frame, dtype=np.float32).reshape(-1) / 32768.0
        residual, ready = self.playback_aec.process(audio)  # conta sempre i frame: la linea del tempo dell'AEC
        if self.playback_aec.has_reference:
            if not ready:
                self.barge_in.preroll.push(audio)
                return  # periodo di calibrazione: senza il ritardo l'eco non e' cancellato
            position = self.playback_aec.position - len(audio)
            reference_level = self.playback_aec.reference_level(position, len(audio))
            pcm = np.clip(residual * 32768.0, -32768, 32767).astype(np.int16).tobytes()
            speech = self.vad_listener.is_speech_pcm(pcm)
            residual_level = float(np.sqrt(np.mean(residual.astype(np.float64) ** 2)))
        else:
            residual, speech, residual_level, reference_level = audio, is_speech, level, 0.0
        turn = self.barge_in.on_frame(residual, speech, residual_level, reference_level, speaking=True)
        if turn is not None:
            self._start_interruption(turn)

    def _start_interruption(self, turn) -> None:
        """Il barge-in ha gia' fermato la voce: si raccoglie cio' che l'utente sta dicendo, pre-roll compreso."""
        preroll = self.barge_in.take_preroll()
        frame_size = VadListener.FRAME_SAMPLES
        pcm = np.clip(preroll * 32768.0, -32768, 32767).astype(np.int16)
        frames = [pcm[i:i + frame_size].reshape(-1, 1) for i in range(0, len(pcm) - frame_size + 1, frame_size)]
        self._interrupted_turn = turn
        self._interrupted_deadline = time.time() + 12.0
        self.listening.arm_command()  # la frase che segue e' per Jake: niente wake word
        self._set_state("listening", "")
        if self.live_transcriber is not None:
            self.live_transcriber.start_utterance()
            self._live_active = True
            for seeded in frames:
                self.live_transcriber.feed(seeded)
        self.vad_listener.begin_utterance(frames)
        self._logger.info("Barge-in: parlato interrotto (turno %s)", turn.turn_id)

    # ---- trascrizione in tempo reale (F2.2.2, F2.2.7) ----------------------------------------

    def _deliver_transcript(self, event: TranscriptEvent) -> None:
        """Un evento di trascrizione (partial o final) verso il bus e verso l'eventuale callback dell'HUD.
        Un partial non e' MAI un comando: qui non arriva mai a `_process_command`.

        Privacy: si pubblica SOLO cio' che e' rivolto a Jake. Il parlato ambientale (una TV, una conversazione in
        stanza) senza la parola di attivazione viene scartato dopo la trascrizione e non deve finire sul bus, che il
        companion server trasmette ai client; la dettatura non si pubblica (puo' contenere qualunque cosa)."""
        if not self._transcript_is_addressed(event.text):
            return
        bus = getattr(self.jake_core, "event_bus", None)
        publish = getattr(bus, "publish", None)
        if callable(publish):
            try:
                publish(to_hud_event(event))
            except Exception:
                self._logger.exception("Errore pubblicando la trascrizione")
        if self.on_transcript is not None:
            try:
                self.on_transcript(event)
            except Exception:
                self._logger.exception("Errore nel callback della trascrizione")

    def _transcript_is_addressed(self, text: str) -> bool:
        if not text.strip():
            return False
        state = self.listening.state
        if state == ListeningState.DICTATION:
            return False
        if state in (ListeningState.COMMAND, ListeningState.FOLLOW_UP, ListeningState.CONFIRMATION):
            return True
        if self._interrupted_turn is not None:
            return True
        return self._match_wake_word(text) is not None

    def _on_utterance_frame(self, frame) -> None:
        live = self.live_transcriber
        if live is None:
            return
        if not self._live_active:
            live.start_utterance()
            self._live_active = True
        live.feed(frame)

    def _on_utterance_end(self) -> None:
        live = self.live_transcriber
        if live is None or not self._live_active:
            return
        self._live_ids = live.end_utterance()
        self._live_active = False

    def _publish_final_transcript(self, text: str) -> None:
        """L'evento FINALE della frase appena trascritta, con lo stesso utterance_id degli eventuali partial."""
        import uuid

        live = self.live_transcriber
        if live is not None and self._live_ids is not None:
            utterance_id, revision = self._live_ids
            self._live_ids = None
            event = live.final_event(text, self.last_confidence, utterance_id, revision)
        else:
            event = TranscriptEvent(uuid.uuid4().hex[:12], 1, "final", text, text, self.last_confidence)
        self._deliver_transcript(event)

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
            self.playback_aec.reset()  # nuova riproduzione: riferimento e calibrazione ripartono
            self.barge_in.begin_response()
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
        self.last_confidence = None
        try:
            with self._stt_lock:
                # confidenza vera solo da un provider che la riporta (WhisperSttProvider): si guarda la CLASSE, cosi'
                # un finto/mock senza il metodo non produce un valore inventato
                if getattr(type(self.stt_provider), "transcribe_detailed", None) is not None:
                    text, self.last_confidence = self.stt_provider.transcribe_detailed(utterance, self.vad_listener.SAMPLE_RATE)
                    return text.strip()
                return self.stt_provider.transcribe(utterance, self.vad_listener.SAMPLE_RATE).strip()
        except Exception:
            self._logger.exception("Errore nella trascrizione vocale")
            return ""

    def _handle_utterance(self, utterance) -> None:
        now = time.time()
        speaking = self._tts_thread is not None and self._tts_thread.is_alive()
        # F2.7: stessa frase gia' registrata dal VAD, riusata per l'identificazione del parlante
        # (nessuna cattura audio in piu') - letta da _process_command sotto, che e' l'unico punto
        # in cui questa frase diventa davvero un comando per core.answer().
        self._last_utterance_audio = utterance

        text = self._transcribe(utterance)
        if not text:
            self._live_ids = None
            self._interrupted_turn = None
            if self.state == "transcribing":
                self._set_state("listening" if now < self._awaiting_command_until else "idle", "")
            return
        self._logger.info("Sentito: %s", text)
        self._publish_final_transcript(text)

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

        # F2.4.5: la frase arriva da un barge-in. "basta"/"no" da soli = fermati e basta; "no, intendevo X" e una
        # nuova richiesta = si esegue il testo utile (senza wake word: l'utente parlava gia' con Jake).
        if self._interrupted_turn is not None:
            self._interrupted_turn = None
            if time.time() <= self._interrupted_deadline:
                outcome = classify_interruption(text)
                if outcome.kind == "stop":
                    self.listening.command_consumed()
                    self._set_state("idle", "")
                    return
                if outcome.kind in ("correction", "new_request") and outcome.remainder:
                    self._process_command(outcome.remainder)
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

    def _identify_speaker_token(self):
        """F2.7 (adozione, prima fetta): None (il caso normale) se non c'e' uno speaker_store, se
        l'audio dell'utterance non e' disponibile, se il riconoscimento fallisce, o se la
        confidenza non e' "high" - un riconoscimento incerto non deve MAI etichettare il turno
        con un profilo indovinato (vedi core/voice/speaker_profile.py::SpeakerHint). Un errore
        qui non deve mai impedire a Jake di rispondere: stesso principio gia' applicato a
        echo_guard/repeat_guard/barge-in in questo stesso file."""
        if self.speaker_store is None or self._last_utterance_audio is None:
            return None
        try:
            features = extract_features(self._last_utterance_audio)
            hint = identify(features, self.speaker_store.profiles())
        except Exception:
            self._logger.exception("Errore identificando la voce")
            return None
        if hint.confidence != "high" or hint.profile_id is None:
            return None
        return set_current_speaker_profile_id(hint.profile_id)

    def _process_command(self, command: str) -> None:
        self.listening.command_consumed()
        print(f"Tu > {command}")
        self._set_state("thinking", command)
        speaker_token = self._identify_speaker_token()
        # F2.6.6: self.last_confidence e' la confidenza VERA gia' riportata da _transcribe()
        # (None per un provider che non la riporta - mai un valore inventato qui). JakeCore la
        # legge per decidere se aggiungere una conferma quando Jake non era sicuro di aver capito
        # bene, vedi core/request_context.py::current_stt_confidence.
        confidence_token = set_current_stt_confidence(self.last_confidence) if self.last_confidence is not None else None
        try:
            response = self.jake_core.answer(command)
        finally:
            if speaker_token is not None:
                reset_current_speaker_profile_id(speaker_token)
            if confidence_token is not None:
                reset_current_stt_confidence(confidence_token)
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
        if self.live_transcriber is not None:
            self.live_transcriber.close()
