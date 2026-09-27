"""Salute del modello locale DENTRO un turno (prova reale del 27/09/2026).

Cosa e' successo: "spiegami come e' fatto un processore" e' stata trascritta bene, ma Ollama era cosi' lento (GPU da
8 GB condivisa con Whisper su CUDA, voce RVC e HUD 3D: elaborazione del prompt a ~40 token/s) che ogni chiamata e'
scaduta. Il turno ha pero' continuato a provarne altre in cascata - classificatore 25 s, agente 60 s, risposta
diretta 40 s due volte - restando in THINKING per quasi tre minuti, e alla fine ha detto "non riesco a contattare
Ollama", che era falso: Ollama rispondeva, era lento.

Qui:
- `classify` distingue server spento, timeout (server vivo ma modello troppo lento), modello mancante, errore interno;
- `diagnose` dice PERCHE' era lento quando lo si puo' sapere (modello solo in parte sulla GPU, GPU quasi piena);
- per turno (`begin_turn`/`end_turn`, un ContextVar) il primo fallimento vale per tutte le chiamate successive dello
  stesso turno: falliscono subito invece di ripetere la stessa attesa. Il turno dopo riparte da zero."""
from __future__ import annotations

import contextvars
import json
import socket
from dataclasses import dataclass
from urllib import error, request

OFFLINE, TIMEOUT, MODEL_MISSING, SERVER_ERROR = "offline", "timeout", "model_missing", "server_error"


@dataclass(frozen=True)
class ModelFailure:
    kind: str
    detail: str = ""
    hint: str = ""


_TURN: contextvars.ContextVar[dict | None] = contextvars.ContextVar("model_turn_health", default=None)


def begin_turn() -> contextvars.Token:
    return _TURN.set({})


def end_turn(token: contextvars.Token) -> None:
    _TURN.reset(token)


def failure() -> ModelFailure | None:
    state = _TURN.get()
    return state.get("failure") if state is not None else None


def record(failure_: ModelFailure) -> None:
    state = _TURN.get()
    if state is not None and state.get("failure") is None and failure_.kind in (OFFLINE, TIMEOUT):
        state["failure"] = failure_  # solo cio' che si ripeterebbe uguale nello stesso turno


def classify(exc: BaseException) -> str:
    """Tipo di fallimento da un'eccezione della chiamata HTTP a Ollama."""
    if isinstance(exc, error.HTTPError):
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace").lower()
        except Exception:
            pass
        if exc.code == 404 or "not found" in body:
            return MODEL_MISSING
        return SERVER_ERROR
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return TIMEOUT
    if isinstance(exc, error.URLError):
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
            return TIMEOUT
        return OFFLINE
    if isinstance(exc, (ConnectionError, OSError)):
        return OFFLINE
    return SERVER_ERROR


def _get_json(url: str, timeout: float) -> dict | None:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def diagnose(base_url: str, kind: str, free_vram_mb=None) -> ModelFailure:
    """Dopo un timeout: il server e' vivo? il modello sta tutto sulla GPU? quanta memoria video resta? Pochi secondi al
    massimo, mai un'altra chiamata al modello. `free_vram_mb`: callable() -> int | None (iniettabile nei test)."""
    if kind != TIMEOUT:
        return ModelFailure(kind)
    if _get_json(f"{base_url}/api/version", 1.5) is None:
        return ModelFailure(OFFLINE, "il server non risponde piu'")
    hints = []
    loaded = _get_json(f"{base_url}/api/ps", 1.5) or {}
    for model in loaded.get("models", []) if isinstance(loaded.get("models"), list) else []:
        size, in_vram = model.get("size"), model.get("size_vram")
        if isinstance(size, int) and isinstance(in_vram, int) and size > 0 and in_vram < size * 0.95:
            hints.append(f"il modello {model.get('name', '')} sta solo in parte sulla GPU ({round(100 * in_vram / size)}%)")
    if free_vram_mb is None:
        from core.model_router import detect_local_hardware

        def free_vram_mb():
            return detect_local_hardware().available_vram_mb
    try:
        free = free_vram_mb()
    except Exception:
        free = None
    if isinstance(free, int) and free < 700:
        hints.append(f"la GPU e' quasi piena ({free} MB liberi): altri programmi la stanno usando")
    return ModelFailure(TIMEOUT, "Ollama risponde ma il modello e' troppo lento", "; ".join(hints))
