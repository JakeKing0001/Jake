"""Esecuzione permanente e contenuta per le skill forgiate (F1.6, "sandbox permanente per
l'esecuzione delle skill installate - Job Object/AppContainer"), in aggiunta a
core/process_sandbox.py, non al suo posto: quel modulo protegge SOLO il passo di validazione
della Forge (un processo usa-e-getta, chiamato una volta), questo protegge OGNI chiamata reale a
una skill gia' installata, per tutta la vita del processo Jake - il gap che il docstring di
process_sandbox.py dichiara esplicitamente ("non l'esecuzione permanente in produzione... che
gira ancora con i privilegi normali dopo l'installazione").

Un Job Object (o un AppContainer) si applica a un PROCESSO, non a una singola chiamata di
funzione dentro il processo di Jake: l'unico modo reale di contenere l'esecuzione di una skill
forgiata e' farla girare in un processo SEPARATO. Rifare questo per ogni singola chiamata
(spawn/exec/kill a ogni invocazione) sarebbe piu' semplice da implementare ma aggiungerebbe una
latenza reale (avvio di un nuovo interprete Python a ogni chiamata) e perderebbe qualunque stato
tra una chiamata e l'altra - **decisione esplicita dell'utente** di preferire invece un worker
PERSISTENTE: un solo processo sandboxato, avviato una volta, che resta vivo e serve tutte le
chiamate successive tramite un protocollo a righe JSON su pipe (vedi core/forge_worker.py per il
protocollo esatto).

Due livelli di contenimento indipendenti, entrambi opt-in per lo stesso principio "degrado
elegante ma mai silenzioso" gia' in process_sandbox.py:
- **Integrita' Low (MIC)**: stesso meccanismo di process_sandbox.py (token duplicato e abbassato
  con SetTokenInformation) - il worker non puo' scrivere su NESSUN oggetto (file, registro) con
  etichetta 'Medium' o superiore, indipendentemente dagli ACL espliciti.
- **Job Object**: nuovo qui - un limite di memoria (JOB_OBJECT_LIMIT_JOB_MEMORY) e di tempo CPU
  totale (JOB_OBJECT_LIMIT_JOB_TIME) applicati dal KERNEL, non da un controllo Python che il
  processo potrebbe eludere. Verificato per davvero (non solo implementato): un'allocazione oltre
  il limite di memoria del Job fallisce con un MemoryError CATTURABILE dentro forge_worker.py (il
  worker sopravvive e continua a servire le chiamate successive - un singolo passo che esagera
  con la memoria non deve buttare giu' il worker condiviso da TUTTE le skill forgiate), non un
  arresto immediato del processo - il limite di tempo CPU e' quindi la difesa per un ciclo
  infinito CPU-bound, NON per una skill semplicemente bloccata/in attesa (es. un `time.sleep()`
  lunghissimo, che non consuma tempo CPU misurabile): per quel caso la difesa e' invoke_timeout_
  seconds (sotto) piu' lo spegnimento forzato di stop() se il worker non risponde in tempo -
  due meccanismi complementari, nessuno dei due basta da solo. JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
  garantisce che chiudere l'handle del Job termini comunque il worker anche se la richiesta di
  arresto esplicita (vedi stop()) fallisse per qualunque motivo - una rete di sicurezza in piu',
  non l'unico meccanismo di arresto.

Se le API di sicurezza Windows non sono disponibili (pywin32 mancante, ambiente che nega la
duplicazione del token), il worker gira comunque - come processo normale, senza NESSUNA delle
due protezioni - con un avviso esplicito (integrity_restricted=False), mai un fallimento
silenzioso: lo stesso principio di process_sandbox.py, verificato con lo stesso test empirico
"il probe/worker malevolo non riesce a scrivere fuori dal proprio canale" quando le protezioni
sono davvero attive."""
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core.skill_result import SkillResult

LOW_INTEGRITY_SID = "S-1-16-4096"
_FORGE_WORKER_PATH = Path(__file__).resolve().with_name("forge_worker.py")

try:
    import win32api
    import win32con
    import win32event
    import win32file
    import win32job
    import win32pipe
    import win32process
    import win32security

    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# Sentinella interna: distingue "il worker e' morto/la pipe si e' chiusa" (mai piu' nessuna
# risposta in arrivo) da "nessuna risposta ancora" (un timeout puo' ancora arrivare) nella coda
# condivisa tra il thread lettore e invoke() sotto.
_WORKER_EXITED = object()


class SandboxedSkillWorker:
    """Avvia e gestisce UN worker persistente (core/forge_worker.py) condiviso da tutte le skill
    forgiate. `project_root`/`plugin_paths` sono passati al worker cosi' com'e' gia' fatto per
    core/forge_probe.py (nessun import relativo, lo script resta eseguibile standalone anche a
    integrita' ridotta)."""

    def __init__(
        self, project_root: str, plugin_paths: list[str], *,
        memory_limit_bytes: int = 256 * 1024 * 1024, cpu_time_limit_seconds: float = 30.0,
        invoke_timeout_seconds: float = 20.0, logger=None,
    ):
        self.project_root = project_root
        self.plugin_paths = list(plugin_paths)
        self.memory_limit_bytes = memory_limit_bytes
        self.cpu_time_limit_seconds = cpu_time_limit_seconds
        self.invoke_timeout_seconds = invoke_timeout_seconds
        self.logger = logger
        self.integrity_restricted = False
        # subprocess.Popen (ripiego) o PyHANDLE (CreateProcessAsUser) - Any: i due rami non
        # condividono un tipo comune utile, e PyHANDLE non e' un tipo importabile da annotare.
        self._process: Any = None
        self._job: Any = None
        self._stdin_write: Optional[Callable[[bytes], None]] = None
        self._responses: queue.Queue = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()  # una sola invoke() alla volta: il protocollo e' seriale

    # ---- ciclo di vita --------------------------------------------------------------------

    def start(self) -> None:
        """Avvia il worker. Prova prima integrita' Low + Job Object (CreateProcessAsUser); se le
        API non sono disponibili o falliscono per qualunque motivo, ripiega su subprocess.Popen
        normale (nessuna protezione, ma mai un avvio negato in silenzio - vedi self.logger)."""
        argv = [sys.executable, str(_FORGE_WORKER_PATH), self.project_root, *self.plugin_paths]
        if _WIN32_AVAILABLE:
            try:
                self._start_sandboxed(argv)
                self.integrity_restricted = True
            except Exception:
                if self.logger:
                    self.logger.exception(
                        "Impossibile avviare il worker skill forgiate a integrita' ridotta: ripiego "
                        "su un processo normale, SENZA protezione OS."
                    )
                self._start_plain(argv)
        else:
            self._start_plain(argv)
        ready = self._read_response(timeout=10)
        if ready is None or ready.get("ready") is not True:
            raise RuntimeError("il worker skill forgiate non ha inviato il segnale di avvio (ready)")

    def _start_plain(self, argv: list[str]) -> None:
        self.integrity_restricted = False
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._process = process
        # stdin/stdout non sono mai None qui: appena richiesti esplicitamente con PIPE sopra -
        # variabili locali invece di process.stdin/process.stdout ripetuti, cosi' mypy non li
        # tratta come Optional a ogni chiamata.
        stdin, stdout = process.stdin, process.stdout
        assert stdin is not None and stdout is not None

        def _write(data: bytes) -> None:
            stdin.write(data)
            stdin.flush()

        self._stdin_write = _write
        self._start_reader(stdout.readline)

    def _start_sandboxed(self, argv: list[str]) -> None:
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.bInheritHandle = True
        child_stdin_read, parent_stdin_write = win32pipe.CreatePipe(sa, 0)
        parent_stdout_read, child_stdout_write = win32pipe.CreatePipe(sa, 0)
        win32api.SetHandleInformation(parent_stdin_write, win32con.HANDLE_FLAG_INHERIT, 0)
        win32api.SetHandleInformation(parent_stdout_read, win32con.HANDLE_FLAG_INHERIT, 0)

        startup_info = win32process.STARTUPINFO()
        startup_info.dwFlags = win32process.STARTF_USESTDHANDLES
        startup_info.hStdInput = child_stdin_read
        startup_info.hStdOutput = child_stdout_write
        startup_info.hStdError = child_stdout_write

        current_token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY | win32con.TOKEN_ADJUST_DEFAULT
            | win32con.TOKEN_ASSIGN_PRIMARY,
        )
        token = win32security.DuplicateTokenEx(
            current_token, win32security.SecurityImpersonation, win32con.TOKEN_ALL_ACCESS,
            win32security.TokenPrimary, None,
        )
        sid = win32security.ConvertStringSidToSid(LOW_INTEGRITY_SID)
        win32security.SetTokenInformation(token, win32security.TokenIntegrityLevel, (sid, 0))

        cmdline = subprocess.list2cmdline(argv)
        cwd = str(Path(self.project_root)) if self.project_root else None
        # CREATE_SUSPENDED: il Job Object (sotto) va assegnato PRIMA che il worker inizi a fare
        # qualunque cosa - assegnarlo dopo aver gia' ripreso il thread lascerebbe una finestra,
        # per quanto breve, in cui il processo gira senza i limiti di risorsa.
        handle_process, handle_thread, _pid, _tid = win32process.CreateProcessAsUser(
            token, argv[0], cmdline, None, None, True, win32process.CREATE_SUSPENDED, None, cwd,
            startup_info,
        )
        child_stdin_read.Close()
        child_stdout_write.Close()
        self._process = handle_process
        self._handle_thread = handle_thread
        self._stdin_write = lambda data: win32file.WriteFile(parent_stdin_write, data)
        self._parent_stdout_read = parent_stdout_read

        try:
            self._job = self._make_job_object()
            win32job.AssignProcessToJobObject(self._job, handle_process)
        finally:
            # Anche se il Job Object non si crea/assegna, il worker deve comunque partire (a
            # integrita' ridotta, solo senza i limiti di risorsa) invece di restare sospeso per
            # sempre - stesso principio "degrado elegante ma non silenzioso" del resto del modulo.
            win32process.ResumeThread(handle_thread)

        self._start_reader(self._read_pipe_line)

    def _make_job_object(self):
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] = (
            win32job.JOB_OBJECT_LIMIT_JOB_MEMORY | win32job.JOB_OBJECT_LIMIT_JOB_TIME
            | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info["JobMemoryLimit"] = self.memory_limit_bytes
        # PerJobUserTimeLimit e' in unita' da 100 nanosecondi (stesso formato FILETIME usato in
        # tutta l'API Win32 per gli intervalli di tempo).
        info["BasicLimitInformation"]["PerJobUserTimeLimit"] = int(self.cpu_time_limit_seconds * 10_000_000)
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        return job

    def _read_pipe_line(self) -> bytes:
        buffer = b""
        while not buffer.endswith(b"\n"):
            try:
                _, chunk = win32file.ReadFile(self._parent_stdout_read, 1)
            except Exception:
                return buffer  # pipe chiusa (worker morto): quello che si e' letto finora, se c'e'
            if not chunk:
                return buffer
            buffer += chunk
        return buffer

    def _start_reader(self, read_line) -> None:
        def _run():
            while True:
                try:
                    line = read_line()
                except Exception:
                    line = b""
                if not line:
                    self._responses.put(_WORKER_EXITED)
                    return
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    self._responses.put(json.loads(stripped))
                except json.JSONDecodeError:
                    continue  # una riga corrotta non deve far perdere il thread lettore

        self._reader_thread = threading.Thread(target=_run, daemon=True)
        self._reader_thread.start()

    def _read_response(self, timeout: float):
        try:
            payload = self._responses.get(timeout=timeout)
        except queue.Empty:
            return None
        return None if payload is _WORKER_EXITED else payload

    # ---- invocazione ------------------------------------------------------------------------

    def invoke(self, intent: str, parameters: dict) -> SkillResult:
        """Invia UNA richiesta e attende UNA risposta, con un timeout. Seriale (un `Lock`): il
        protocollo a righe non distingue le risposte per richiesta, quindi due invoke()
        concorrenti si scambierebbero le risposte - accettabile, le skill forgiate sono
        tipicamente utility semplici e rare, non un percorso ad alta concorrenza."""
        with self._lock:
            if not self.is_alive() or self._stdin_write is None:
                return SkillResult(success=False, data={}, error="SANDBOX_WORKER_UNAVAILABLE")
            try:
                self._stdin_write(
                    (json.dumps({"intent": intent, "parameters": parameters}, ensure_ascii=False) + "\n").encode("utf-8")
                )
            except Exception:
                return SkillResult(success=False, data={}, error="SANDBOX_WORKER_UNAVAILABLE")
            response = self._read_response(timeout=self.invoke_timeout_seconds)
            if response is None:
                return SkillResult(success=False, data={}, error="SANDBOX_WORKER_TIMEOUT")
            return SkillResult(
                success=bool(response.get("success")), data=response.get("data") or {},
                error=response.get("error"),
            )

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        if _WIN32_AVAILABLE and self.integrity_restricted:
            return win32event.WaitForSingleObject(self._process, 0) == win32event.WAIT_TIMEOUT
        return self._process.poll() is None

    def stop(self, timeout: float = 3.0) -> None:
        """Chiede l'arresto pulito (un messaggio di shutdown), aspetta fino a `timeout`, poi
        termina a forza se ancora vivo - stesso schema "prova a chiedere, poi impona" gia' usato
        per i quattro scheduler in background (F1.8.5). Chiudere l'handle del Job Object DOPO
        (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) e' una rete di sicurezza in piu', non l'unico
        meccanismo di arresto."""
        if self._process is None:
            return
        try:
            if self._stdin_write is not None:
                self._stdin_write(json.dumps({"shutdown": True}).encode("utf-8") + b"\n")
        except Exception:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.is_alive():
            time.sleep(0.05)
        if self.is_alive():
            if self.logger:
                self.logger.warning("Il worker skill forgiate non si e' fermato in tempo: terminato a forza.")
            self._terminate()
        if self._job is not None:
            self._job.Close()
            self._job = None

    def _terminate(self) -> None:
        try:
            if _WIN32_AVAILABLE and self.integrity_restricted:
                win32process.TerminateProcess(self._process, 1)
            else:
                self._process.kill()
        except Exception:
            pass
