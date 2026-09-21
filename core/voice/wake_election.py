"""Un solo dispositivo risponde a una wake word sentita da piu' dispositivi (F2.3.7).

Quando il PC e un satellite (cucina, telefono) sentono lo stesso "Jake", se rispondono tutti
l'utente si ritrova la risposta doppia o, peggio, due azioni. Ogni dispositivo che ha sentito
segnala una candidatura (`submit`) con un punteggio di qualita' dell'ascolto; passata una breve
finestra di raccolta, `resolve` sceglie UN vincitore e `should_respond` dice agli altri di tacere.

Criteri, in ordine: punteggio piu' alto; a parita' (entro `tie_margin`) il dispositivo che era
gia' attivo (continuita' della conversazione, vedi core/device_registry.py); poi la priorita'
configurata; infine l'id, cosi' la scelta e' deterministica e due dispositivi non possono
eleggersi entrambi. Il modulo non parla in rete: chi lo usa fa arrivare le candidature (per
esempio dal companion, F7.1) e applica il risultato al registro con `claim_winner`."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class WakeCandidate:
    device_id: str
    score: float  # 0-1: livello/confidenza dell'ascolto su QUEL dispositivo
    heard_at: float


class WakeElection:
    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        window_s: float = 0.5,
        tie_margin: float = 0.05,
        hold_s: float = 3.0,
        priority: dict[str, int] | None = None,
        active_device: Callable[[], str | None] | None = None,
    ) -> None:
        self._clock = clock
        self.window_s = window_s
        self.tie_margin = tie_margin
        self.hold_s = hold_s
        self.priority = priority or {}
        self._active_device = active_device
        self._candidates: dict[str, WakeCandidate] = {}
        self._round_started: float | None = None
        self._winner: str | None = None
        self._decided_at: float | None = None

    def submit(self, device_id: str, score: float, heard_at: float | None = None) -> None:
        """Candidatura di un dispositivo per il round in corso. Una nuova candidatura dello stesso
        dispositivo tiene la migliore. Se il round precedente si e' chiuso, ne comincia uno nuovo."""
        now = self._clock()
        if self._winner is not None:
            # Round deciso: una candidatura tardiva (stesso "Jake", sentito un istante dopo da un
            # dispositivo lontano) NON lo riapre; dopo `hold_s` una nuova candidatura e' invece una
            # nuova attivazione e comincia un round nuovo.
            if self._decided_at is not None and now - self._decided_at >= self.hold_s:
                self._reset()
            else:
                return
        if self._round_started is None:
            self._round_started = now
        score = min(1.0, max(0.0, float(score)))
        current = self._candidates.get(device_id)
        if current is None or score > current.score:
            self._candidates[device_id] = WakeCandidate(device_id, score, now if heard_at is None else heard_at)

    def resolve(self) -> str | None:
        """Il vincitore, oppure None finche' la finestra di raccolta e' ancora aperta (o se non ci
        sono candidati). Dopo la chiusura ritorna sempre lo stesso vincitore fino al round dopo."""
        if self._winner is not None:
            return self._winner
        if self._round_started is None or not self._candidates:
            return None
        if self._clock() - self._round_started < self.window_s:
            return None
        self._winner = self._choose()
        self._decided_at = self._clock()
        return self._winner

    def should_respond(self, device_id: str) -> bool | None:
        """True se `device_id` ha vinto, False se ha perso, None se la finestra e' ancora aperta."""
        winner = self.resolve()
        if winner is None:
            return None
        return winner == device_id

    def _choose(self) -> str:
        candidates = list(self._candidates.values())
        best_score = max(c.score for c in candidates)
        contenders = [c for c in candidates if best_score - c.score <= self.tie_margin]
        active = self._active_device() if self._active_device is not None else None
        contenders.sort(key=lambda c: (
            0 if c.device_id == active else 1,  # continuita': il dispositivo gia' attivo
            -self.priority.get(c.device_id, 0),
            -c.score,
            c.device_id,
        ))
        return contenders[0].device_id

    def _reset(self) -> None:
        self._candidates = {}
        self._round_started = None
        self._winner = None
        self._decided_at = None

    def new_round(self) -> None:
        """Chiude il round in corso (da chiamare a risposta conclusa)."""
        self._reset()

    def claim_winner(self, registry, names: dict[str, str] | None = None) -> str | None:
        """Assegna la sessione al vincitore nel `DeviceRegistry`. Ritorna l'id vincitore o None se
        non ancora deciso. Il registro decide se il passaggio e' consentito."""
        winner = self.resolve()
        if winner is None:
            return None
        registry.claim(winner, (names or {}).get(winner, ""))
        return winner
