"""Protocollo eventi (v4.9.1, HUD Engine 2.0 - fase "UI separation"): il vocabolario con cui
JakeCore comunica il proprio stato a qualsiasi presentazione esterna - l'HUD PySide6 attuale,
un futuro HUD nativo C++/Qt6/QML (vedi hud/native/README.md), o un'app companion su un altro
dispositivo (fase 5.8) - senza che quella presentazione debba sapere come funziona Ollama, gli
agenti o la memoria.

Prima questo "protocollo" esisteva solo come stringhe libere passate a SessionHooks.set_state
(vedi core/session_hooks.py), leggibile SOLO da un processo Python nello stesso interprete
tramite un segnale Qt (core/gui/hud/app.py: self.bridge.state.emit(state, detail)) - un
consumatore alla volta, mai trasmissibile in rete. Qui il vocabolario viene reso esplicito
(un'enum, non stringhe libere) e serializzabile (JSON), cosi' core/event_bus.py e
core/companion_server.py possono trasmetterlo fuori dal processo."""
import json
import time
from dataclasses import dataclass, field
from enum import Enum

from core.version import PROTOCOL_VERSION


class EventType(str, Enum):
    HUD_SHOW = "HUD_SHOW"
    HUD_HIDE = "HUD_HIDE"
    USER_MESSAGE = "USER_MESSAGE"
    JAKE_MESSAGE = "JAKE_MESSAGE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    ERROR = "ERROR"
    AGENT_STEP = "AGENT_STEP"
    NOTIFICATION = "NOTIFICATION"
    # In piu' rispetto al vocabolario base: stati gia' distinti da tempo in SessionHooks/HUD
    # PySide6 (core/gui/hud/app.py) che meritano un evento proprio invece di essere schiacciati
    # su uno dei precedenti.
    IDLE = "IDLE"
    DICTATION = "DICTATION"
    PAUSED = "PAUSED"
    DEVICE_HANDOFF = "DEVICE_HANDOFF"  # v5.9, Ambient Computing: un altro dispositivo e' ora attivo


# Traduce gli stati gia' in uso da WakeWordSession/SessionHooks.set_state (stringhe libere,
# core/voice/wake_word_session.py) nell'EventType corrispondente, cosi' i publish() aggiunti in
# JakeCore/WakeWordSession per il nuovo bus (v4.9.1) restano coerenti con quel vocabolario
# esistente invece di inventarne uno parallelo scollegato.
LEGACY_STATE_TO_EVENT_TYPE = {
    "idle": EventType.IDLE,
    "listening": EventType.LISTENING,
    "transcribing": EventType.LISTENING,
    "thinking": EventType.THINKING,
    "working": EventType.EXECUTING,
    "responding": EventType.JAKE_MESSAGE,
    "speaking": EventType.JAKE_MESSAGE,
    "dictation": EventType.DICTATION,
    "paused": EventType.PAUSED,
    "notify": EventType.NOTIFICATION,
    "error": EventType.ERROR,
    "exit": EventType.HUD_HIDE,
}


@dataclass
class HudEvent:
    type: EventType
    payload: dict = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": PROTOCOL_VERSION,
                "type": self.type.value,
                "payload": self.payload,
                "at": self.at,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "HudEvent":
        data = json.loads(raw)
        schema_version = data.get("schema_version", PROTOCOL_VERSION)
        if schema_version != PROTOCOL_VERSION:
            raise ValueError(
                f"versione protocollo non supportata: {schema_version}; attesa {PROTOCOL_VERSION}"
            )
        return cls(type=EventType(data["type"]), payload=data.get("payload") or {}, at=data.get("at", time.time()))

    @classmethod
    def from_legacy_state(cls, state: str, detail: str = "") -> "HudEvent | None":
        """Costruisce un HudEvent da una chiamata SessionHooks.set_state(state, detail)
        esistente (vedi LEGACY_STATE_TO_EVENT_TYPE): stato non riconosciuto -> None."""
        event_type = LEGACY_STATE_TO_EVENT_TYPE.get(state)
        if event_type is None:
            return None
        return cls(type=event_type, payload={"detail": detail} if detail else {})
