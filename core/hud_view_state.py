"""Come un client HUD interpreta il flusso di eventi (F4.1, criterio d'uscita: "client Python finto e
JakeClient C++ superano la stessa suite di fixture").

`HudViewState` e' il riferimento eseguibile: parte da uno stato vuoto e applica gli eventi SSE (le
righe JSON di `HudEvent.to_json`) uno alla volta. `hud/native/src/HudEventReducer.cpp` implementa le
STESSE regole, e le due implementazioni leggono le stesse fixture (`tests/fixtures/hud_contract.json`).

Regole:
- `schema_version` diversa: evento rifiutato e stream da interrompere (finestra di compatibilita' zero,
  F4.1.6); JSON non valido, `type` sconosciuto o `payload` non oggetto: evento ignorato e contato, mai
  trasformato in uno "stato" col nome dell'evento;
- ordine (F4.4.6): in una connessione i `sequence_id` crescono; uno <= all'ultimo visto e' un duplicato
  e si ignora, TRANNE come primo evento di una nuova connessione, dove significa che il server e'
  ripartito da capo: il contatore si azzera e l'evento si applica;
- stato mostrato: solo gli eventi di stato lo cambiano (IDLE, LISTENING, THINKING, EXECUTING,
  DICTATION, PAUSED, ERROR; AGENT_STEP -> EXECUTING; JAKE_MESSAGE con testo -> IDLE, senza testo, cioe'
  lo stato legacy "speaking"/"responding", -> SPEAKING; CONFIRMATION in sospeso -> WAITING, risolta ->
  IDLE se si era in WAITING). Trascrizione, microfono, notifiche, prove e
  diagnosi hanno ciascuna il proprio spazio e non toccano lo stato;
- trascrizione: una revisione piu' vecchia della stessa frase si ignora, un partial dopo il final
  della stessa frase anche; un partial resta provvisorio (mai un comando).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.hud_protocol import PROTOCOL_VERSION, EventType

MAX_ITEMS = 20

_STATE_EVENTS = {"IDLE", "LISTENING", "THINKING", "EXECUTING", "DICTATION", "PAUSED"}
_KNOWN_TYPES = {event_type.value for event_type in EventType}


@dataclass
class HudViewState:
    state: str = "IDLE"
    visible: bool = True
    mic_open: bool = False
    mic_discarding: bool = False
    mic_reason: str = ""
    transcript_utterance: str = ""
    transcript_revision: int = 0
    transcript_text: str = ""
    transcript_final: bool = False
    messages: list[list[str]] = field(default_factory=list)
    notifications: list[list[str]] = field(default_factory=list)
    last_error: str = ""
    step_index: int = 0
    step_description: str = ""
    evidence: list[dict[str, str]] = field(default_factory=list)
    inspection_verdict: str = ""
    inspection_reason: str = ""
    active_device: str = ""
    confirmation_pending: bool = False
    confirmation_intent: str = ""
    confirmation_reason: str = ""
    confirmation_risk: str = ""
    confirmation_external: bool = False
    confirmation_trace_id: str = ""
    last_sequence_id: int = 0
    ignored: int = 0
    incompatible: bool = False
    _fresh_connection: bool = field(default=False, repr=False)

    # ---- ingresso ------------------------------------------------------------------------------

    def connection_started(self) -> None:
        """Nuova connessione SSE (anche un reconnect)."""
        self._fresh_connection = True
        self.incompatible = False

    def apply_line(self, line: str) -> str:
        """Applica una riga `data:` SSE. Ritorna "applied", "ignored" o "incompatible"."""
        try:
            data = json.loads(line)
        except (TypeError, ValueError):
            return self._ignore()
        if not isinstance(data, dict):
            return self._ignore()
        return self.apply(data)

    def apply(self, data: dict[str, Any]) -> str:
        if data.get("schema_version") != PROTOCOL_VERSION:
            self.incompatible = True
            return "incompatible"
        event_type = data.get("type")
        payload = data.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(event_type, str) or event_type not in _KNOWN_TYPES or not isinstance(payload, dict):
            return self._ignore()
        sequence_id = data.get("sequence_id", 0)
        if not isinstance(sequence_id, int) or isinstance(sequence_id, bool):
            sequence_id = 0
        fresh, self._fresh_connection = self._fresh_connection, False
        if sequence_id > 0:
            if sequence_id <= self.last_sequence_id and not fresh:
                return self._ignore()  # duplicato o fuori ordine nella stessa connessione
            self.last_sequence_id = sequence_id  # (su una connessione nuova: server ripartito)
        trace_id = data.get("trace_id")
        self._reduce(event_type, payload, trace_id if isinstance(trace_id, str) else "")
        return "applied"

    def _ignore(self) -> str:
        self.ignored += 1
        return "ignored"

    # ---- regole per tipo ----------------------------------------------------------------------

    def _reduce(self, event_type: str, payload: dict[str, Any], trace_id: str) -> None:
        if event_type in _STATE_EVENTS:
            self.state = event_type
        elif event_type == "USER_MESSAGE":
            self._push(self.messages, ["user", _text(payload, "text")])
        elif event_type == "JAKE_MESSAGE":
            text = _text(payload, "text")
            if text:
                self._push(self.messages, ["jake", text])
                self.state = "IDLE"
            else:
                self.state = "SPEAKING"
        elif event_type == "AGENT_STEP":
            self.step_index = _int(payload, "step")
            self.step_description = _text(payload, "description")
            self.state = "EXECUTING"
        elif event_type == "ERROR":
            self.last_error = _text(payload, "detail")
            self.state = "ERROR"
        elif event_type == "NOTIFICATION":
            self._push(self.notifications, [_text(payload, "kind"), _text(payload, "text")])
        elif event_type == "HUD_SHOW":
            self.visible = True
        elif event_type == "HUD_HIDE":
            self.visible = False
        elif event_type == "DEVICE_HANDOFF":
            self.active_device = _text(payload, "to")
        elif event_type == "MIC_STATE":
            self.mic_open = payload.get("open") is True
            self.mic_discarding = payload.get("discarding") is True
            self.mic_reason = _text(payload, "reason")
        elif event_type == "TRANSCRIPT":
            self._transcript(payload)
        elif event_type in ("UNDO", "VERIFICATION"):
            # trace_id (F4.1.1): collega la prova alle ricevute del ledger della stessa esecuzione
            self._push(self.evidence, {"kind": event_type.lower(), "intent": _text(payload, "intent"),
                                       "verified": _text(payload, "verified"), "trace_id": trace_id})
        elif event_type == "CONFIRMATION":
            if payload.get("pending") is True:
                self.confirmation_pending = True
                self.confirmation_intent = _text(payload, "intent")
                self.confirmation_reason = _text(payload, "reason")
                self.confirmation_risk = _text(payload, "risk")
                self.confirmation_external = payload.get("external_source") is True
                self.confirmation_trace_id = trace_id
                self.state = "WAITING"
            else:
                self.confirmation_pending = False
                self.confirmation_intent = self.confirmation_reason = self.confirmation_risk = ""
                self.confirmation_external = False
                self.confirmation_trace_id = ""
                if self.state == "WAITING":
                    self.state = "IDLE"
        elif event_type == "SELECTOR_INSPECTION":
            inspection = payload.get("inspection")
            inspection = inspection if isinstance(inspection, dict) else {}
            self.inspection_verdict = _text(inspection, "verdict")
            self.inspection_reason = _text(inspection, "reason") or _text(payload, "error")

    def _transcript(self, payload: dict[str, Any]) -> None:
        kind = payload.get("kind")
        utterance = _text(payload, "utterance_id")
        revision = _int(payload, "revision")
        if kind not in ("partial", "final") or not utterance:
            self.ignored += 1
            return
        if utterance == self.transcript_utterance:
            if self.transcript_final or revision <= self.transcript_revision:
                return  # revisione vecchia, o partial arrivato dopo il final
        self.transcript_utterance = utterance
        self.transcript_revision = revision
        self.transcript_text = _text(payload, "text")
        self.transcript_final = kind == "final"

    @staticmethod
    def _push(items: list, item) -> None:
        items.append(item)
        del items[:-MAX_ITEMS]

    # ---- confronto con le fixture ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {name: value for name, value in self.__dict__.items() if not name.startswith("_")}


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
