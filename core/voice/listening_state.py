"""Stati di ascolto espliciti, protezioni da eco/replay e indicatore del microfono
(F2.3.3, F2.3.4, F2.3.5).

Prima di questo modulo gli "stati" di `WakeWordSession` erano quattro variabili sparse
(`dictation_active`, `paused_until`, `_awaiting_command_until`, `_follow_up_until`) e la
domanda "questa frase, senza la parola di attivazione, va eseguita?" si ricostruiva leggendo
`_handle_utterance`. Qui gli stati sono un'enum, le transizioni sono metodi e la regola e' scritta
una volta sola, con gli stessi tempi e la stessa semantica di prima (le finestre di comando e di
follow-up restano quelle di `WakeWordSession`).

Le protezioni (`EchoGuard`, `WakeCooldown`, `RepeatGuard`) rispondono a una domanda precisa: l'audio
che ha attivato Jake viene da una persona o da un altoparlante (la voce di Jake stesso, una TV, un
video)? Nessuna e' infallibile e nessuna finge di esserlo: riducono i falsi risvegli, non li
eliminano; la misura vera resta il benchmark su hardware reale (F2.1/F2.3 criterio di uscita)."""
from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from enum import Enum

from core.hud_protocol import EventType, HudEvent


class ListeningState(str, Enum):
    WAKE = "wake"  # attende la parola di attivazione
    COMMAND = "command"  # ha sentito solo "Jake": la prossima frase e' un comando
    FOLLOW_UP = "follow_up"  # subito dopo una risposta: si puo' continuare senza ripetere "Jake"
    CONFIRMATION = "confirmation"  # c'e' un'azione in attesa di si'/no
    DICTATION = "dictation"  # tutto cio' che si dice viene scritto
    SLEEP = "sleep"  # in pausa: si sveglia solo con la frase di risveglio


class ListeningStateMachine:
    COMMAND_WAIT_SECONDS = 8.0
    FOLLOW_UP_SECONDS = 6.0
    CONFIRMATION_WAIT_SECONDS = 20.0

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        on_change: Callable[[ListeningState, ListeningState], None] | None = None,
        command_wait_s: float | None = None,
        follow_up_s: float | None = None,
        confirmation_wait_s: float | None = None,
    ) -> None:
        self._clock = clock
        self.on_change = on_change
        self.command_wait_s = self.COMMAND_WAIT_SECONDS if command_wait_s is None else command_wait_s
        self.follow_up_s = self.FOLLOW_UP_SECONDS if follow_up_s is None else follow_up_s
        self.confirmation_wait_s = self.CONFIRMATION_WAIT_SECONDS if confirmation_wait_s is None else confirmation_wait_s
        self.dictating = False
        self.sleep_until = 0.0
        self.command_until = 0.0
        self.follow_up_until = 0.0
        self._pending_action = False
        self._last_state = ListeningState.WAKE

    # ---- stato derivato ------------------------------------------------------------------

    def set_pending_action(self, pending: bool) -> None:
        self._pending_action = pending
        self._notify()

    @property
    def state(self) -> ListeningState:
        """Priorita': SLEEP > DICTATION > COMMAND > FOLLOW_UP > CONFIRMATION > WAKE."""
        now = self._clock()
        if now < self.sleep_until:
            return ListeningState.SLEEP
        if self.dictating:
            return ListeningState.DICTATION
        if now < self.command_until:
            return ListeningState.COMMAND
        if now < self.follow_up_until:
            return ListeningState.FOLLOW_UP
        if self._pending_action and now < self.follow_up_until + self.confirmation_wait_s:
            return ListeningState.CONFIRMATION
        return ListeningState.WAKE

    def accepts_without_wake_word(self) -> bool:
        """Una frase senza la parola di attivazione va eseguita come comando? Solo in COMMAND,
        FOLLOW_UP e CONFIRMATION: DICTATION la scrive, SLEEP la ignora, WAKE la scarta."""
        return self.state in (ListeningState.COMMAND, ListeningState.FOLLOW_UP, ListeningState.CONFIRMATION)

    def _notify(self) -> None:
        current = self.state
        if current != self._last_state:
            previous, self._last_state = self._last_state, current
            if self.on_change is not None:
                self.on_change(previous, current)

    # ---- transizioni ---------------------------------------------------------------------

    def arm_command(self) -> None:
        """Ha sentito "Jake" (o click sull'orb): la prossima frase e' un comando."""
        self.command_until = self._clock() + self.command_wait_s
        self._notify()

    def command_consumed(self) -> None:
        """Un comando e' stato preso in carico: chiude le finestre di comando e follow-up."""
        self.command_until = 0.0
        self.follow_up_until = 0.0
        self._notify()

    def open_follow_up(self) -> None:
        """Dopo una risposta: per `follow_up_s` si puo' continuare senza dire "Jake". Con 0 non
        apre nulla (follow-up disattivato)."""
        self.follow_up_until = self._clock() + self.follow_up_s if self.follow_up_s > 0 else 0.0
        self._notify()

    def start_dictation(self) -> None:
        self.dictating = True
        self._notify()

    def stop_dictation(self) -> None:
        self.dictating = False
        self._notify()

    def sleep(self, minutes: float) -> None:
        self.sleep_until = self._clock() + max(1.0, minutes) * 60
        self._notify()

    def wake_up(self) -> None:
        self.sleep_until = 0.0
        self._notify()

    def tick(self) -> ListeningState:
        """Da chiamare periodicamente: fa scattare `on_change` anche per le scadenze a tempo."""
        self._notify()
        return self.state


# ---- protezioni da eco e replay -----------------------------------------------------------------

_WORDS = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _WORDS.findall(text.lower())


class EchoGuard:
    """Riconosce la propria voce: se una frase trascritta e' (quasi) contenuta in cio' che Jake ha
    appena detto, e' l'eco dell'altoparlante nel microfono, non l'utente. Frasi di meno di 3 parole
    non si giudicano mai eco: "Jake" da solo potrebbe essere l'utente anche se Jake l'ha detto."""

    def __init__(self, clock: Callable[[], float] = time.time, ttl_s: float = 12.0, threshold: float = 0.8, min_words: int = 3) -> None:
        self._clock = clock
        self.ttl_s = ttl_s
        self.threshold = threshold
        self.min_words = min_words
        self._spoken: deque[tuple[float, set[str]]] = deque(maxlen=20)

    def note_spoken(self, text: str) -> None:
        tokens = set(_tokens(text))
        if tokens:
            self._spoken.append((self._clock(), tokens))

    def is_echo(self, transcript: str) -> bool:
        words = _tokens(transcript)
        if len(words) < self.min_words:
            return False
        now = self._clock()
        for at, tokens in self._spoken:
            if now - at > self.ttl_s:
                continue
            overlap = sum(1 for word in words if word in tokens) / len(words)
            if overlap >= self.threshold:
                return True
        return False


class WakeCooldown:
    """Dopo un'attivazione accettata, ignora nuove attivazioni per `seconds`: una TV che ripete il
    nome o un doppio rilevamento della stessa frase non deve riattivare Jake a raffica."""

    def __init__(self, clock: Callable[[], float] = time.time, seconds: float = 1.5) -> None:
        self._clock = clock
        self.seconds = seconds
        self._last = -1e18

    def allow(self) -> bool:
        return self._clock() - self._last >= self.seconds

    def register(self) -> None:
        self._last = self._clock()


class RepeatGuard:
    """Una frase identica sentita due volte in `window_s` secondi non l'ha detta una persona che
    aspettava una risposta: e' un loop (spot, video, altoparlante). La prima passa, la ripetizione
    entro la finestra no."""

    def __init__(self, clock: Callable[[], float] = time.time, window_s: float = 2.5) -> None:
        self._clock = clock
        self.window_s = window_s
        self._last_text = ""
        self._last_at = -1e18

    def is_replay(self, transcript: str) -> bool:
        text = " ".join(_tokens(transcript))
        now = self._clock()
        replay = bool(text) and text == self._last_text and now - self._last_at < self.window_s
        self._last_text, self._last_at = text, now
        return replay


# ---- indicatore del microfono ---------------------------------------------------------------------

class MicIndicator:
    """Stato pubblico del microfono (F2.3.5): ogni superficie attiva (HUD, companion) lo mostra,
    quindi cambia solo attraverso un evento sul bus e mai in silenzio. `open` = lo stream di cattura
    e' aperto; `discarding` = e' aperto ma i frame vengono scartati (Jake sta parlando): l'indicatore
    non deve mai dire "spento" se il dispositivo sta ancora ascoltando."""

    def __init__(self, publish: Callable[[HudEvent], None], clock: Callable[[], float] = time.time) -> None:
        self._publish = publish
        self._clock = clock
        self.open = False
        self.discarding = False
        self.reason = "off"
        self.since = 0.0

    def update(self, open: bool, reason: str, discarding: bool = False) -> bool:
        """Aggiorna e pubblica solo se qualcosa e' cambiato. True se ha pubblicato."""
        if (open, reason, discarding) == (self.open, self.reason, self.discarding):
            return False
        self.open, self.reason, self.discarding = open, reason, discarding
        self.since = self._clock()
        self._publish(HudEvent(EventType.MIC_STATE, {
            "open": open, "discarding": discarding, "reason": reason, "since": self.since,
        }))
        return True

    def payload(self) -> dict:
        return {"open": self.open, "discarding": self.discarding, "reason": self.reason, "since": self.since}
