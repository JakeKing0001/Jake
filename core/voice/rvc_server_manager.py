import subprocess
import time
from pathlib import Path

from core.voice.rvc_client import RvcClient


class RvcServerManager:
    """Avvia/ferma il server locale di conversione vocale RVC come sottoprocesso, con
    l'interprete Python dedicato in .venv-rvc (dipendenze pesanti/datate isolate dal venv
    principale di Jake). Se il server e' gia' in ascolto (avviato a mano o da un run precedente),
    lo riusa cosi' com'e' invece di avviarne un secondo."""

    def __init__(self, model_name: str, port: int = 5050, project_root: Path = None):
        self.model_name = model_name
        self.port = port
        self.project_root = project_root or Path(__file__).resolve().parent.parent.parent
        self.client = RvcClient(base_url=f"http://127.0.0.1:{port}")
        self._process = None

    def is_installed(self) -> bool:
        python_exe = self.project_root / ".venv-rvc" / "Scripts" / "python.exe"
        server_script = self.project_root / "core" / "voice" / "rvc_server.py"
        model_dir = self.project_root / "rvc_models" / self.model_name
        return python_exe.is_file() and server_script.is_file() and model_dir.is_dir()

    def ensure_running(self, timeout: float = 90) -> bool:
        """Avvia il server se non e' gia' attivo. Restituisce True se pronto entro il timeout."""
        if self.client.is_available():
            return True
        if not self.is_installed():
            return False

        python_exe = self.project_root / ".venv-rvc" / "Scripts" / "python.exe"
        server_script = self.project_root / "core" / "voice" / "rvc_server.py"
        self._process = subprocess.Popen(
            [str(python_exe), str(server_script), "--model", self.model_name, "--port", str(self.port)],
            cwd=str(self.project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.client.is_available():
                return True
            if self._process.poll() is not None:
                return False
            time.sleep(1)
        return False

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
