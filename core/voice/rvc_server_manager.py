import subprocess
import time
import threading
from pathlib import Path

from core.voice.rvc_client import RvcClient


class RvcServerManager:
    """Avvia/ferma il server locale di conversione vocale RVC come sottoprocesso, con
    l'interprete Python dedicato in .venv-rvc (dipendenze pesanti/datate isolate dal venv
    principale di Jake). Se il server e' gia' in ascolto (avviato a mano o da un run precedente),
    lo riusa cosi' com'e' invece di avviarne un secondo."""

    IDLE_CHECK_SECONDS = 30.0

    def __init__(self, model_name: str, port: int = 5050, project_root: Path = None,
                 idle_shutdown_seconds: float | None = None, clock=time.monotonic):
        """`idle_shutdown_seconds`: se Jake non parla per questo tempo, il server avviato da noi
        viene chiuso e torch + modello RVC (1-2 GB) liberano la RAM. None/0 = mai."""
        self.model_name = model_name
        self.port = port
        self.project_root = project_root or Path(__file__).resolve().parent.parent.parent
        self.client = RvcClient(base_url=f"http://127.0.0.1:{port}")
        self._process = None
        self._start_lock = threading.Lock()
        self._prewarm_thread = None
        self.idle_shutdown_seconds = idle_shutdown_seconds or None
        self._clock = clock
        self._last_used = clock()
        self._idle_stopped = False
        self._held = False
        self._watchdog: threading.Thread | None = None
        self._watchdog_stop = threading.Event()

    def is_installed(self) -> bool:
        python_exe = self.project_root / ".venv-rvc" / "Scripts" / "python.exe"
        server_script = self.project_root / "core" / "voice" / "rvc_server.py"
        model_dir = self.project_root / "rvc_models" / self.model_name
        return python_exe.is_file() and server_script.is_file() and model_dir.is_dir()

    def ensure_running(self, timeout: float = 90) -> bool:
        """Avvia il server se non e' gia' attivo.

        Thread-safe: il prewarm e la prima speak() possono arrivare qui
        contemporaneamente senza avviare due server RVC.
        """
        if self.client.is_available():
            self.touch()
            return True

        with self._start_lock:
            # Potrebbe essere diventato disponibile mentre aspettavamo il lock.
            if self.client.is_available():
                return True

            if not self.is_installed():
                return False

            python_exe = self.project_root / ".venv-rvc" / "Scripts" / "python.exe"
            server_script = self.project_root / "core" / "voice" / "rvc_server.py"

            self._process = subprocess.Popen(
                [
                    str(python_exe),
                    str(server_script),
                    "--model",
                    self.model_name,
                    "--port",
                    str(self.port),
                ],
                cwd=str(self.project_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            deadline = time.time() + timeout

            while time.time() < deadline:
                if self.client.is_available():
                    self._idle_stopped = False
                    self.touch()
                    self._start_watchdog()
                    return True

                if self._process.poll() is not None:
                    return False

                time.sleep(1)

            return False

    def touch(self) -> None:
        self._last_used = self._clock()

    @property
    def idle_stopped(self) -> bool:
        """Vero se il server e' stato chiuso per inattivita' e non e' ancora ripartito."""
        return self._idle_stopped

    @property
    def held(self) -> bool:
        """Vero mentre la GPU e' ceduta ad altro (core/gpu_yield.py): il server resta spento, si parla con la voce
        di base e nessun prewarm lo riaccende."""
        return self._held

    def hold(self) -> None:
        with self._start_lock:
            self._held = True
            self.stop()

    def release(self) -> None:
        self._held = False

    def stop_if_idle(self) -> bool:
        """Chiude il server SOLO se l'abbiamo avviato noi e nessuno lo usa da troppo."""
        if not self.idle_shutdown_seconds or self._process is None or self._process.poll() is not None:
            return False
        if self._clock() - self._last_used < self.idle_shutdown_seconds:
            return False
        with self._start_lock:
            self.stop()
            self._idle_stopped = True
        return True

    def _start_watchdog(self) -> None:
        if not self.idle_shutdown_seconds or (self._watchdog is not None and self._watchdog.is_alive()):
            return

        def watch() -> None:
            while not self._watchdog_stop.wait(self.IDLE_CHECK_SECONDS):
                self.stop_if_idle()

        self._watchdog = threading.Thread(target=watch, name="rvc-idle-watchdog", daemon=True)
        self._watchdog.start()

    def prewarm(self) -> None:
        """Carica RVC in background durante l'avvio di Jake.

        Non blocca il bootstrap. Se speak() arriva prima che abbia finito,
        ensure_running() aspettera' lo stesso avvio grazie a _start_lock.
        """
        if self._held:
            return
        if self._prewarm_thread is not None and self._prewarm_thread.is_alive():
            return

        self._prewarm_thread = threading.Thread(
            target=self.ensure_running,
            name=f"rvc-prewarm-{self.model_name}",
            daemon=True,
        )
        self._prewarm_thread.start()

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
