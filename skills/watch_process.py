"""F6.7 (task monitor vero): "avvisami quando finisce la build". Jake sorveglia un processo GIA' in esecuzione e,
quando termina, dice quanto e' durato e com'e' andato (codice d'uscita: diverso da zero = errore/crash). Nessun
polling: un thread daemon aspetta la fine del processo. La notifica passa dalla stessa pipeline delle altre
(JakeCore.present_notification), come richiesta esplicita dell'utente. Sola osservazione: nessuna azione sul
processo."""
import os
import threading
import time

from core.skill_result import SkillResult

MAX_WATCHES = 5


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} secondi"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


class WatchProcessSkill:
    metadata = {
        "intent": "WATCH_PROCESS",
        "description": (
            "Avvisa quando un programma o processo gia' in esecuzione termina (build, installazione, script lungo), "
            "dicendo quanto e' durato e se e' finito con un errore. Per 'avvisami quando finisce la build'."
        ),
        "parameters": {
            "process": {"type": "string", "required": True,
                        "description": "Nome del processo o del programma da sorvegliare (es. 'msbuild', 'npm', 'python')."},
        },
    }

    def __init__(self, core, process_iter=None, clock=time.monotonic):
        self.core = core
        self._process_iter = process_iter
        self._clock = clock
        self._lock = threading.Lock()
        self.watching: dict[int, str] = {}

    def _processes(self):
        if self._process_iter is not None:
            return self._process_iter()
        import psutil

        return psutil.process_iter(["pid", "name", "create_time"])

    def execute(self, parameters: dict = None):
        query = str((parameters or {}).get("process") or "").strip().lower().removesuffix(".exe")
        if not query:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        own = os.getpid()
        candidates = [p for p in self._processes()
                      if p.pid != own and query in str(p.info.get("name") or "").lower()]
        if not candidates:
            return SkillResult(success=False, data={"process": query}, error="NOT_FOUND")
        target = max(candidates, key=lambda p: p.info.get("create_time") or 0)  # il piu' recente
        with self._lock:
            if len(self.watching) >= MAX_WATCHES:
                return SkillResult(success=False, data={}, error="TOO_MANY_WATCHES")
            name = str(target.info.get("name") or query)
            self.watching[target.pid] = name
        started = self._clock()
        threading.Thread(target=self._wait, args=(target, name, started), name="jake-watch-process", daemon=True).start()
        return SkillResult(success=True, data={"process": name, "pid": target.pid})

    def _wait(self, process, name: str, started: float) -> None:
        try:
            code = process.wait()
        except Exception:
            code = None
        finally:
            with self._lock:
                self.watching.pop(process.pid, None)
        elapsed = _duration(self._clock() - started)
        if code is None:
            outcome = "esito non disponibile"
        elif code == 0:
            outcome = "completato senza errori"
        else:
            outcome = f"terminato con un errore (codice {code})"
        self.core.present_notification("reminder", f"{name} e' finito dopo {elapsed}: {outcome}.")
