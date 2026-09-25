import socket
from urllib import request

from core.turn_cancellation import cancellable_call


def is_online(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """Verifica rapidamente se e' disponibile una connessione di rete in uscita."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read(target, timeout: float) -> bytes:
    with request.urlopen(target, timeout=timeout) as response:
        return response.read()


def read_url(target, timeout: float) -> bytes:
    """Corpo di una richiesta HTTP (URL o `urllib.request.Request`).

    Dentro un turno vocale annullabile la lettura gira su un thread a parte: "Jake, basta" smette
    di aspettare subito (TurnCancelled) e una risposta tardiva viene scartata. Fuori da un turno
    e' esattamente `urlopen(...).read()`, con le stesse eccezioni."""
    return cancellable_call(_read, target, timeout, name="jake-http")
