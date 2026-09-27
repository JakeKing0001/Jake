"""Avvio e sorveglianza dell'HUD nativo (F4.8.2: "avviare core e HUD in ordine e gestire crash separati").

Il core parte per primo; l'HUD nativo (`hud/native/build/JakeHud.exe`) viene lanciato solo DOPO che il
companion server e' in ascolto, con l'indirizzo giusto (`--jake-url`). I due processi restano separati:
- se l'HUD va in crash (codice d'uscita diverso da 0) viene riavviato con attese crescenti, al massimo
  `max_restarts` volte in `window_s` secondi, poi si smette e lo si scrive nel log (niente loop infiniti);
- se l'utente lo chiude (uscita 0) non viene riaperto;
- il core non aspetta mai l'HUD: la sorveglianza gira su un thread daemon;
- allo shutdown del core l'HUD viene chiuso (terminate, poi kill se non esce entro qualche secondo).
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from core.logger import get_logger

DEFAULT_EXE = Path(__file__).resolve().parent.parent / "hud" / "native" / "build" / "JakeHud.exe"
# Identita' companion dell'HUD nativo locale: una credenziale per-dispositivo come quella di un telefono
# accoppiato (F7), ruotata a ogni avvio del core, con capability minime e revocata allo shutdown.
NATIVE_HUD_DEVICE_ID = "native-hud-local"
NATIVE_HUD_CREDENTIAL_TTL_S = 30 * 24 * 3600


class NativeHudSupervisor:
    def __init__(
        self,
        exe_path: Path,
        base_url: str,
        *,
        max_restarts: int = 3,
        window_s: float = 300.0,
        backoff_s: tuple[float, ...] = (2.0, 5.0, 15.0),
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        credentials: dict | None = None,
    ) -> None:
        self.exe_path = Path(exe_path)
        self.base_url = base_url
        # {"device_id", "token"}: consegnate all'HUD sul suo stdin (mai nella riga di comando, visibile a
        # qualunque processo dello stesso utente, ne' in config), una volta per ogni avvio.
        self._credentials = credentials
        self.max_restarts = max_restarts
        self.window_s = window_s
        self.backoff_s = backoff_s
        self._popen = popen
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._process = None
        self._thread: threading.Thread | None = None
        self._restarts: list[float] = []
        self.gave_up = False
        self._logger = get_logger()

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def start(self) -> bool:
        """Lancia l'HUD e la sua sorveglianza. False (con un avviso nel log) se l'eseguibile non c'e'."""
        if not self.exe_path.exists():
            self._logger.warning("HUD nativo non trovato in %s: compilalo con cmake (hud/native/README.md)", self.exe_path)
            return False
        self._stop.clear()
        if not self._spawn():
            return False
        self._thread = threading.Thread(target=self._watch, name="jake-native-hud", daemon=True)
        self._thread.start()
        return True

    def _spawn(self) -> bool:
        args = [str(self.exe_path), "--jake-url", self.base_url]
        if self._credentials:
            args.append("--credentials-stdin")
        try:
            process = self._popen(args, cwd=str(self.exe_path.parent),
                                  stdin=subprocess.PIPE if self._credentials else None)
            if self._credentials:
                process.stdin.write((json.dumps(self._credentials) + "\n").encode("utf-8"))
                process.stdin.close()
        except OSError:
            self._logger.exception("Impossibile avviare l'HUD nativo")
            return False
        with self._lock:
            self._process = process
        return True

    def _watch(self) -> None:
        while not self._stop.is_set():
            process = self._process
            if process is None:
                return
            code = process.wait()
            if self._stop.is_set():
                return
            if code == 0:
                self._logger.info("HUD nativo chiuso dall'utente: non viene riaperto")
                return
            now = self._clock()
            self._restarts = [at for at in self._restarts if now - at < self.window_s]
            if len(self._restarts) >= self.max_restarts:
                self.gave_up = True
                self._logger.error("HUD nativo in crash %d volte in %.0f s: non lo riavvio piu'", len(self._restarts) + 1,
                                   self.window_s)
                return
            delay = self.backoff_s[min(len(self._restarts), len(self.backoff_s) - 1)]
            self._restarts.append(now)
            self._logger.warning("HUD nativo terminato con codice %s: lo riavvio fra %.0f s", code, delay)
            if self._stop.wait(delay) or not self._spawn():
                return

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
