"""Contesto per-richiesta thread-safe (F1.2.3/F1.8.1, fondamenta): propaga l'identita' del
dispositivo companion mittente lungo l'intera catena di chiamate sincrona (companion_server ->
JakeCore.answer -> _resolve_and_execute/TaskAgent/PlanExecutor -> ActionLedger) SENZA aggiungere
un parametro `device_id` a ogni metodo intermedio - decine di firme (answer, _process,
_handle_confirmation, _finalize_pending_action, _resolve_and_execute, _run_agent, _try_plan...)
avrebbero dovuto propagarlo a mano solo per farlo arrivare ai quattro chokepoint che scrivono
davvero una ActionReceipt.

Usa `contextvars.ContextVar` invece di un attributo di istanza su JakeCore (es.
`self.current_device_id`): JakeCore e' UN'unica istanza condivisa sia dal loop voce sia da
core/companion_server.py (un `ThreadingHTTPServer` - ogni richiesta HTTP gira sul proprio thread),
quindi un attributo mutabile sarebbe una race condition tra due richieste concorrenti da
dispositivi diversi - esattamente la classe di buco gia' trovata e corretta piu' volte in questa
sessione (F1.8.1, F1.8.7). Un `ContextVar` e' invece isolato per thread by design: un thread
nuovo (ogni richiesta di un `ThreadingHTTPServer` ne crea uno) parte SEMPRE dal valore di default,
mai da quello impostato da un altro thread in corso - verificato empiricamente, non solo assunto
dalla documentazione, prima di scegliere questo approccio.

None (il default, e il comportamento di chi non chiama mai `set_current_device_id`) significa
"nessun dispositivo companion noto per questa richiesta" - il caso normale per il loop voce
locale, che resta un canale implicito separato (nessuna modifica li'), e per le automazioni
lanciate da TriggerScheduler sul proprio thread in background."""
import contextvars

_current_device_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_device_id", default=None)


def current_device_id() -> str | None:
    """Il device_id del dispositivo companion che ha originato la richiesta in corso su QUESTO
    thread, o None se non impostato (comando vocale locale, automazione in background, o una
    richiesta companion senza device_id nel body - vedi
    core/companion_server.py::_Handler._handle_command)."""
    return _current_device_id.get()


def set_current_device_id(device_id: str | None) -> contextvars.Token:
    """Imposta il device_id per il resto dell'esecuzione su QUESTO thread. Restituisce un Token
    da passare a reset_current_device_id() per ripristinare il valore precedente al termine della
    richiesta."""
    return _current_device_id.set(device_id)


def reset_current_device_id(token: contextvars.Token) -> None:
    _current_device_id.reset(token)
