"""Avvio di Jake Server: `python -m jake_server`.

Configurazione solo da variabili d'ambiente (nel container le imposta il Dockerfile/docker-compose.yml):
- `JAKE_SERVER_HOST`: interfaccia di ascolto. Default 127.0.0.1, come il server companion: ascoltare in LAN e' una
  scelta esplicita di chi lo avvia (il container usa 0.0.0.0 e la porta pubblicata decide l'interfaccia dell'host).
- `JAKE_SERVER_PORT`: porta, default 8770.

Si chiude pulito su SIGTERM (`docker stop`) e SIGINT (Ctrl+C)."""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from core.version import VERSION
from jake_server import DEFAULT_PORT, SERVICE_NAME
from jake_server.health import HealthServer


def _port_from_env() -> int:
    raw = os.environ.get("JAKE_SERVER_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    port = int(raw)
    if not 0 < port < 65536:
        raise ValueError(f"JAKE_SERVER_PORT fuori intervallo: {port}")
    return port


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    logger = logging.getLogger("jake_server")
    host = os.environ.get("JAKE_SERVER_HOST", "").strip() or "127.0.0.1"
    try:
        port = _port_from_env()
    except ValueError as exc:
        logger.error("Configurazione non valida: %s", exc)
        return 2

    server = HealthServer(host=host, port=port)
    server.start()
    logger.info("%s %s in ascolto su %s:%d (solo GET /health)", SERVICE_NAME, VERSION, host, server.port)

    stop = threading.Event()

    def _request_stop(signum, _frame):
        logger.info("Segnale %s ricevuto, chiusura", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    while not stop.wait(1.0):
        pass
    server.stop()
    logger.info("%s chiuso", SERVICE_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
