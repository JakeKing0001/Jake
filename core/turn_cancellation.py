"""Cancellazione del SINGOLO turno vocale ("Jake, basta"), distinta dal KillSwitch globale.

Il worker vocale imposta un `threading.Event` per il turno in un ContextVar; tutto cio' che gira
nello stesso thread di `JakeCore.answer()` (skill, agente, piano, client Ollama, visione) lo vede.
Il KillSwitch resta l'arresto d'emergenza di tutto (automazioni comprese): questo modulo non lo
tocca mai.

Due garanzie:
- attesa interrompibile: `cancellable_call` sposta una chiamata bloccante (HTTP al modello, alla
  visione) su un thread daemon e smette di aspettarla appena il turno viene annullato. La chiamata
  gia' partita puo' finire in background, ma il suo risultato viene scartato;
- nessun effetto dopo l'annullamento: `raise_if_cancelled` e' chiamata dai punti che producono
  effetti (dispatcher delle skill, click/digitazione, pattern UIA) subito prima di agire.

`TurnCancelled` deriva da BaseException (come `asyncio.CancelledError`): i tanti `except Exception`
di degrado del progetto non devono trasformare un annullamento in un "errore, riprovo" o in un
ripiego che continua a lavorare. Fuori da un turno annullabile (testo, HUD, automazioni) nessuna di
queste funzioni solleva mai.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from collections.abc import Callable, Generator
from typing import TypeVar

T = TypeVar("T")

_current_turn_cancel_event: contextvars.ContextVar[threading.Event | None] = (
    contextvars.ContextVar(
        "current_turn_cancel_event",
        default=None,
    )
)

# Ogni quanto un'attesa interrompibile ricontrolla l'evento: abbastanza breve da restare sotto
# la soglia percettiva, abbastanza lungo da non consumare CPU.
POLL_SECONDS = 0.05


class TurnCancelled(BaseException):
    """Il turno corrente e' stato annullato dall'utente: smettere di lavorare, senza ripieghi."""


def current_turn_cancel_event() -> threading.Event | None:
    """Evento di cancellazione del singolo turno corrente."""
    return _current_turn_cancel_event.get()


def current_turn_cancelled() -> bool:
    event = current_turn_cancel_event()
    return event is not None and event.is_set()


def set_current_turn_cancel_event(
    event: threading.Event | None,
) -> contextvars.Token:
    return _current_turn_cancel_event.set(event)


def reset_current_turn_cancel_event(
    token: contextvars.Token,
) -> None:
    _current_turn_cancel_event.reset(token)


def raise_if_cancelled() -> None:
    """Ultimo controllo prima di un effetto (click, digitazione, skill)."""
    if current_turn_cancelled():
        raise TurnCancelled()


@contextlib.contextmanager
def cancellation_suspended() -> Generator[None, None, None]:
    """Esegue il blocco come fuori da un turno annullabile.

    Serve SOLO alle compensazioni (rollback degli effetti gia' prodotti da un compito annullato):
    annullare un turno non deve impedire di rimettere a posto cio' che il turno aveva gia' fatto.
    """
    token = _current_turn_cancel_event.set(None)
    try:
        yield
    finally:
        _current_turn_cancel_event.reset(token)


def cancellable_sleep(seconds: float) -> None:
    """`time.sleep` che si interrompe con TurnCancelled appena il turno viene annullato."""
    event = current_turn_cancel_event()
    if event is None:
        time.sleep(seconds)
        return
    if event.wait(max(0.0, seconds)):
        raise TurnCancelled()


def cancellable_call(fn: Callable[..., T], *args, name: str = "jake-cancellable-call", **kwargs) -> T:
    """Esegue `fn(*args, **kwargs)` smettendo di aspettarla se il turno viene annullato.

    Fuori da un turno annullabile e' una chiamata diretta, nello stesso thread. Dentro un turno la
    chiamata gira su un thread daemon (con una copia del contesto, cosi' log e request context
    restano coerenti) e il chiamante attende a passi di POLL_SECONDS: all'annullamento solleva
    TurnCancelled subito. Un risultato arrivato DOPO l'annullamento viene scartato anch'esso.
    """
    event = current_turn_cancel_event()
    if event is None:
        return fn(*args, **kwargs)
    if event.is_set():
        raise TurnCancelled()

    done = threading.Event()
    box: dict[str, object] = {}
    context = contextvars.copy_context()

    def worker() -> None:
        try:
            box["value"] = context.run(fn, *args, **kwargs)
        except BaseException as exc:  # consegnato al chiamante, mai perso in un thread
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=worker, name=name, daemon=True).start()
    while not done.wait(POLL_SECONDS):
        if event.is_set():
            raise TurnCancelled()
    if event.is_set():
        raise TurnCancelled()
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["value"]  # type: ignore[return-value]
