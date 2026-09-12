import json
import subprocess
import uuid
from pathlib import Path

from core.skill_result import SkillResult


class RunPythonScriptSkill:
    """Esegue uno script Python con l'interprete corrente (sys.executable): a differenza di
    RUN_COMMAND non passa per la shell di sistema, solo per file .py specifici. Richiede comunque
    sempre conferma, come RUN_COMMAND: uno script puo' fare altrettanto danno di un comando."""

    metadata = {
        "intent": "RUN_PYTHON_SCRIPT",
        "description": "Esegue uno script Python e restituisce il suo output.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Percorso dello script .py da eseguire."},
        },
    }

    def execute(self, parameters: dict = None):
        import sys

        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_file():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        if not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "path": raw_path,
                    "message": f"Confermi di voler eseguire lo script {raw_path}?",
                    "confirm_parameters": {"path": raw_path, "confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        try:
            result = subprocess.run(
                [sys.executable, str(path)], capture_output=True, timeout=30, text=True, encoding="utf-8", errors="ignore",
            )
        except subprocess.TimeoutExpired:
            return SkillResult(success=False, data={"path": raw_path}, error="TIMEOUT")
        except Exception:
            return SkillResult(success=False, data={"path": raw_path}, error="OPERATION_FAILED")

        output = (result.stdout or result.stderr or "").strip()[:1500]
        # Stesso buco reale corretto in skills/run_command.py: un codice di uscita diverso da
        # zero (l'eccezione dello script non catturata, un errore di sintassi...) veniva
        # comunque riportato come success=True.
        return SkillResult(
            success=result.returncode == 0,
            data={"path": raw_path, "output": output, "return_code": result.returncode},
            error=None if result.returncode == 0 else "NONZERO_EXIT",
        )


class FormatJsonSkill:
    metadata = {
        "intent": "FORMAT_JSON",
        "description": "Formatta (rende leggibile) una stringa JSON.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Testo JSON da formattare."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return SkillResult(success=False, data={}, error="INVALID_JSON")

        return SkillResult(success=True, data={"formatted": json.dumps(parsed, indent=2, ensure_ascii=False)})


class CountLinesOfCodeSkill:
    metadata = {
        "intent": "COUNT_LINES_OF_CODE",
        "description": "Conta le righe di codice in una cartella di progetto, per estensione di file.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Percorso della cartella del progetto."},
            "extension": {"type": "string", "required": False, "description": "Estensione da contare, es. '.py'. Se omessa conta tutti i file di testo comuni."},
        },
    }

    DEFAULT_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".html", ".css"}
    SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        extension = (parameters.get("extension") or "").strip().lower()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_dir():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        extensions = {extension} if extension else self.DEFAULT_EXTENSIONS
        total_lines = 0
        total_files = 0
        for file_path in path.rglob("*"):
            if any(part in self.SKIP_DIRS for part in file_path.parts):
                continue
            if file_path.is_file() and file_path.suffix.lower() in extensions:
                try:
                    total_lines += sum(1 for _ in file_path.open(encoding="utf-8", errors="ignore"))
                    total_files += 1
                except OSError:
                    continue

        return SkillResult(success=True, data={"path": raw_path, "lines": total_lines, "files": total_files})


class GenerateUuidSkill:
    metadata = {
        "intent": "GENERATE_UUID",
        "description": "Genera un identificatore univoco (UUID), utile per sviluppatori.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        return SkillResult(success=True, data={"uuid": str(uuid.uuid4())})


class CheckPortInUseSkill:
    metadata = {
        "intent": "CHECK_PORT_IN_USE",
        "description": "Verifica se una porta TCP locale e' occupata da un processo.",
        "parameters": {
            "port": {"type": "integer", "required": True, "description": "Numero di porta da controllare."},
        },
    }

    def execute(self, parameters: dict = None):
        import psutil

        parameters = parameters or {}
        port = parameters.get("port")
        if not isinstance(port, int):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        for connection in psutil.net_connections(kind="inet"):
            if connection.laddr and connection.laddr.port == port and connection.status == "LISTEN":
                process_name = ""
                if connection.pid:
                    try:
                        process_name = psutil.Process(connection.pid).name()
                    except psutil.Error:
                        pass
                return SkillResult(success=True, data={"port": port, "in_use": True, "process": process_name, "pid": connection.pid})

        return SkillResult(success=True, data={"port": port, "in_use": False})


class KillProcessByPortSkill:
    """Termina il processo in ascolto su una porta: azione irreversibile su un processo
    potenzialmente non identificato dall'utente per nome, quindi richiede sempre conferma.

    F1.3.2 ("prove forti per... processi"): prima di questa correzione, `success=True` veniva
    restituito subito dopo aver CHIESTO la terminazione (`psutil.Process.terminate()`), senza
    aspettare che il processo fosse davvero morto - `terminate()` invia solo la richiesta, non
    garantisce che sia gia' avvenuta quando la chiamata ritorna. Ora attende fino a
    `TERMINATE_WAIT_SECONDS` che il processo esca per davvero prima di dichiarare successo;
    se non muore in tempo, riporta onestamente un fallimento invece di affermare un effetto mai
    confermato. `data["pid"]` (assente prima) permette a un futuro verificatore indipendente
    (core/execution_safety.py::INTENT_SAFETY_REGISTRY) di ricontrollare lo stesso fatto."""

    TERMINATE_WAIT_SECONDS = 3

    metadata = {
        "intent": "KILL_PROCESS_BY_PORT",
        "description": "Termina il processo che sta occupando una porta TCP specifica.",
        "parameters": {
            "port": {"type": "integer", "required": True, "description": "Numero di porta il cui processo va terminato."},
        },
    }

    def execute(self, parameters: dict = None):
        import psutil

        parameters = parameters or {}
        port = parameters.get("port")
        if not isinstance(port, int):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        target_pid = None
        for connection in psutil.net_connections(kind="inet"):
            if connection.laddr and connection.laddr.port == port and connection.status == "LISTEN":
                target_pid = connection.pid
                break

        if target_pid is None:
            return SkillResult(success=False, data={"port": port}, error="NOT_FOUND")

        try:
            process_name = psutil.Process(target_pid).name()
        except psutil.Error:
            process_name = str(target_pid)

        if not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "port": port,
                    "message": f"Confermi di voler terminare '{process_name}' sulla porta {port}?",
                    "confirm_parameters": {"port": port, "confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        try:
            process = psutil.Process(target_pid)
            process.terminate()
            process.wait(timeout=self.TERMINATE_WAIT_SECONDS)
        except psutil.NoSuchProcess:
            pass  # gia' terminato per conto suo tra la richiesta e l'attesa: comunque un successo
        except psutil.TimeoutExpired:
            return SkillResult(
                success=False, data={"port": port, "pid": target_pid, "process": process_name},
                error="OPERATION_FAILED",
            )
        except psutil.Error:
            return SkillResult(success=False, data={"port": port}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"port": port, "pid": target_pid, "process": process_name})
