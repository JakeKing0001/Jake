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
    # F1.3.8 ("esporre undo e prove a HUD/companion tramite eventi versionati"): prima di questi
    # due, un rollback (core/execution_safety.py::rollback_effect) o una verifica indipendente
    # dell'effetto (F1.3.3, verify_effect) erano visibili SOLO nel ledger (data/jake_ledger.jsonl)
    # - un HUD o un'app companion non aveva modo di saperlo in tempo reale, solo rileggendo il
    # ledger dopo. Payload di UNDO: {"intent": str}; di VERIFICATION: {"intent": str, "verified":
    # str} (uno dei tre valori di core/action_ledger.py::VERIFICATION_VERIFIED/UNVERIFIED/FAILED).
    UNDO = "UNDO"
    VERIFICATION = "VERIFICATION"
    # F2.2.7: trascrizione in tempo reale per HUD/sottotitoli/companion. Payload versionato a
    # parte (`transcript_version`, vedi core/voice/streaming_stt.py::TranscriptEvent.to_payload):
    # utterance_id, revision, kind ("partial"|"final"), text, stable_text, confidence. Un client
    # deve trattare `partial` come provvisorio e mai come comando: solo `final` lo e'.
    TRANSCRIPT = "TRANSCRIPT"
    # F2.3.5: indicatore di ascolto sempre visibile su ogni superficie attiva. Payload: {"open":
    # bool (lo stream di cattura e' aperto), "discarding": bool (aperto ma i frame si scartano,
    # es. mentre Jake parla - non e' "spento"), "reason": str, "since": float}. Vedi
    # core/voice/listening_state.py::MicIndicator, che pubblica solo quando qualcosa cambia.
    MIC_STATE = "MIC_STATE"
    # F3.3.6: diagnosi dell'inspector del selettore per un passo di Computer Use non riuscito
    # (core/computer_use/inspector.py). Payload: {"procedure": str, "step": int, "error": str,
    # "inspection": {"verdict", "reason", "chosen", "alternatives": [{name, control_type,
    # automation_id, score, reason}]}}. Solo metadati UI, mai testo scritto dall'utente.
    SELECTOR_INSPECTION = "SELECTOR_INSPECTION"


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
    # F4.1.1 ("versionare HudEvent, aggiungere sequence id..."): 0 prima della pubblicazione -
    # non un evento fantasma, un valore che non e' mai stato assegnato ancora. Il valore VERO
    # viene assegnato da core/event_bus.py::EventBus.publish() (l'unico punto per cui OGNI
    # HudEvent transita prima di raggiungere un iscritto), non dal chiamante che lo costruisce:
    # solo il bus conosce l'ordine GLOBALE di pubblicazione tra produttori diversi (JakeCore,
    # companion_server...). Un client (HUD nativo, companion) puo' confrontare due sequence_id
    # consecutivi per accorgersi di un evento perso (coda satura, vedi F1.8.6) o - dopo un
    # riconnect (F4.1.3, non ancora affrontato) - riprendere da dove aveva lasciato.
    sequence_id: int = 0
    # F4.1.1 (resto - "aggiungere... trace id"): a differenza di sequence_id sopra, NON assegnato
    # da EventBus.publish() - il chiamante che gia' conosce il trace_id di un'esecuzione reale
    # (JakeCore._execute_command/_run_agent, PlanOutcome.trace_id, F1.7.2) lo passa al momento
    # della costruzione. None (il default) significa "nessuna esecuzione specifica da correlare",
    # non un valore mancante per errore - il caso normale per un evento di stato che non deriva da
    # un comando preciso (IDLE, LISTENING, DEVICE_HANDOFF). Prima fetta: solo NOTIFICATION (gia'
    # lo portava, ma nel payload - F1.7.2) e UNDO/VERIFICATION (gia' correlati a un trace_id reale
    # nel ledger, mai esposto sul bus eventi) lo popolano davvero; AGENT_STEP e USER_MESSAGE/
    # JAKE_MESSAGE/ERROR restano None - richiederebbero rispettivamente un nuovo parametro sulla
    # callback on_step e un modo di risalire al trace_id di UN turno quando piu' percorsi interni
    # (_execute_command/_run_agent/_try_plan) ne generano uno ciascuno in modo indipendente,
    # lavoro non affrontato qui.
    trace_id: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": PROTOCOL_VERSION,
                "type": self.type.value,
                "payload": self.payload,
                "at": self.at,
                "sequence_id": self.sequence_id,
                "trace_id": self.trace_id,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "HudEvent":
        # F4.1.4 ("contract test per ogni payload malformato"): un evento arriva qui da una fonte
        # esterna (SSE via core/companion_server.py, un client di terze parti) - ogni pezzo del
        # JSON viene validato per FORMA prima di costruire l'oggetto, non solo per presenza,
        # cosi' un input malformato fallisce qui con un errore chiaro invece di produrre un
        # HudEvent con un campo del tipo sbagliato che rompe un chiamante lontano e confuso.
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"HudEvent.from_json: atteso un oggetto JSON, ricevuto {type(data).__name__}")
        if "type" not in data:
            raise ValueError("HudEvent.from_json: campo 'type' obbligatorio mancante")
        schema_version = data.get("schema_version", PROTOCOL_VERSION)
        if schema_version != PROTOCOL_VERSION:
            raise ValueError(
                f"versione protocollo non supportata: {schema_version}; attesa {PROTOCOL_VERSION}"
            )
        payload = data.get("payload")
        if payload is None:
            payload = {}
        elif not isinstance(payload, dict):
            raise ValueError(f"HudEvent.from_json: 'payload' deve essere un oggetto JSON, ricevuto {type(payload).__name__}")
        return cls(
            type=EventType(data["type"]), payload=payload, at=data.get("at", time.time()),
            sequence_id=data.get("sequence_id", 0), trace_id=data.get("trace_id"),
        )

    @classmethod
    def from_legacy_state(cls, state: str, detail: str = "") -> "HudEvent | None":
        """Costruisce un HudEvent da una chiamata SessionHooks.set_state(state, detail)
        esistente (vedi LEGACY_STATE_TO_EVENT_TYPE): stato non riconosciuto -> None."""
        event_type = LEGACY_STATE_TO_EVENT_TYPE.get(state)
        if event_type is None:
            return None
        return cls(type=event_type, payload={"detail": detail} if detail else {})
