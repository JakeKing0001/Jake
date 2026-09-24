from __future__ import annotations

import contextvars
import threading


_current_turn_cancel_event: contextvars.ContextVar[threading.Event | None] = (
    contextvars.ContextVar(
        "current_turn_cancel_event",
        default=None,
    )
)


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