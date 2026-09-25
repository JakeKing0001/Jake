import subprocess

from core.command_safety import check_command_safety
from core.skill_result import SkillResult
from core.turn_cancellation import current_turn_cancel_event, current_turn_cancelled

MAX_OUTPUT_CHARS = 1500
COMMAND_TIMEOUT_SECONDS = 30
KILL_SWITCH_POLL_SECONDS = 0.2


class RunCommandSkill:
    """Esegue un comando da riga di comando qualsiasi (shell di sistema). E' la skill piu'
    potente e piu' rischiosa di Jake (puo' fare letteralmente qualsiasi cosa possa fare un
    comando da terminale): richiede sempre conferma esplicita col comando per esteso, cosi'
    l'utente puo' accorgersi subito di una trascrizione vocale sbagliata prima che venga eseguita.

    F1.8.3 ("propagare cancellazione dal kill switch a... subprocess"): buco reale, riprodotto
    prima del fix - `subprocess.run(..., timeout=30)` e' una chiamata bloccante che TaskAgent/
    PlanExecutor non possono interrompere (controllano `kill_switch.is_active()` solo TRA un
    passo e il successivo, vedi core/kill_switch.py), quindi un comando lungo continuava a girare
    fino alla fine (o al timeout di 30s) anche con l'utente che aveva gia' premuto il kill switch.
    `self.kill_switch` e' iniettato da `JakeCore.__init__` DOPO la costruzione della skill (stesso
    pattern gia' usato per `plan_executor.kill_switch`), resta `None` nei test e in ogni contesto
    che non lo passa - in quel caso il comportamento e' IDENTICO a prima (`subprocess.run`
    bloccante). Quando presente, il comando gira su un `Popen` sondato periodicamente: se il kill
    switch scatta mentre il comando e' ancora vivo, il processo viene terminato e la skill
    restituisce `KILLED` (stessa categoria "annullamento voluto dall'utente" gia' usata dal kill
    switch a livello di agente, vedi `ERROR_CATEGORY_USER_CANCELLED` in core/action_ledger.py).
    F1.8.3 (residuo dichiarato, ora chiuso) - kill dell'intero process tree: `process.kill()` da
    solo termina solo il processo della shell (`shell=True`, cmd.exe), non un eventuale nipote che
    il comando avesse lanciato (es. "python -c ..." lanciato da cmd.exe e' figlio DI cmd.exe, non
    di RunCommandSkill). Riprodotto per davvero prima della correzione: un comando che a sua volta
    lancia un sotto-processo lento restava vivo (verificato con psutil.pid_exists sul PID del
    nipote) anche dopo che il kill switch aveva gia' fermato cmd.exe. Corretto assegnando il
    processo appena avviato a un Job Object Windows con JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (vedi
    _make_kill_tree_job): un processo assegnato a un job vi aggiunge automaticamente ogni figlio
    che genera (comportamento di default, a meno che il figlio non chieda esplicitamente
    CREATE_BREAKAWAY_FROM_JOB - nessun comando lanciato da qui lo fa), quindi terminare il job
    (win32job.TerminateJobObject) termina l'intero albero in un colpo solo, non solo cmd.exe.
    Finestra residua nota e accettata, non azzerabile senza riscrivere il lancio con
    CREATE_SUSPENDED + ripresa manuale del thread (subprocess.Popen non espone l'handle del thread
    primario per farlo): se cmd.exe genera gia' un figlio nei pochissimi istanti tra Popen() e
    l'assegnazione al job, quel figlio precocissimo non viene catturato - non il caso rilevante in
    pratica (cio' che conta e' il processo ancora vivo QUANDO il kill switch scatta, non uno gia'
    terminato nei primi millisecondi). Un fallimento nella creazione/assegnazione del Job Object
    (pywin32 assente, OpenProcess negato...) degrada silenziosamente al solo `process.kill()` di
    prima - mai un'eccezione che interrompe l'esecuzione del comando."""

    metadata = {
        "intent": "RUN_COMMAND",
        "description": "Esegue un comando da riga di comando (terminale) e restituisce l'output. "
        "Usalo solo per richieste esplicite tipo 'esegui il comando...', 'lancia da terminale...'. "
        "Non usarlo mai per azioni gia' coperte da un'altra capacita' (aprire app, file, ecc.).",
        "parameters": {
            "command": {
                "type": "string",
                "required": True,
                "description": "Il comando da eseguire, cosi' come detto dall'utente.",
            },
        },
    }

    def __init__(self):
        # F1.8.3: None finche' JakeCore non lo inietta dopo aver creato il kill switch
        # condiviso (stesso pattern di plan_executor.kill_switch) - vedi il docstring sopra.
        self.kill_switch = None

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        command = (parameters.get("command") or "").strip()
        if not command:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        blocked_reason = check_command_safety(command)
        if blocked_reason:
            return SkillResult(
                success=False,
                data={"command": command, "reason": blocked_reason,
                      "message": f"Non eseguo questo comando: sembra {blocked_reason}. Se ti serve davvero, usalo dal terminale direttamente."},
                error="BLOCKED",
            )

        if not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "command": command,
                    "message": f"Confermi di voler eseguire questo comando: \"{command}\"?",
                    "confirm_parameters": {"command": command, "confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        if self.kill_switch is None and current_turn_cancel_event() is None:
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True, timeout=COMMAND_TIMEOUT_SECONDS,
                    text=True, encoding="utf-8", errors="ignore",
                )
            except subprocess.TimeoutExpired:
                return SkillResult(success=False, data={"command": command}, error="TIMEOUT")
            except Exception:
                return SkillResult(success=False, data={"command": command}, error="OPERATION_FAILED")
            return self._build_result(command, result.returncode, result.stdout, result.stderr)

        return self._execute_cancellable(command)

    def _execute_cancellable(self, command: str) -> SkillResult:
        try:
            process = subprocess.Popen(
                command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="ignore",
            )
        except Exception:
            return SkillResult(success=False, data={"command": command}, error="OPERATION_FAILED")

        job = self._make_kill_tree_job(process)
        elapsed = 0.0
        while True:
            try:
                stdout, stderr = process.communicate(timeout=KILL_SWITCH_POLL_SECONDS)
                return self._build_result(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if self.kill_switch is not None and self.kill_switch.is_active():
                    return self._kill_and_abandon(process, command, "KILLED", job)
                if current_turn_cancelled():
                    # "Jake, basta" sul solo turno vocale: si ferma questo comando, non il kill switch.
                    return self._kill_and_abandon(process, command, "CANCELLED", job)
                elapsed += KILL_SWITCH_POLL_SECONDS
                if elapsed >= COMMAND_TIMEOUT_SECONDS:
                    return self._kill_and_abandon(process, command, "TIMEOUT", job)

    @staticmethod
    def _make_kill_tree_job(process: subprocess.Popen):
        """Vedi il docstring della classe per il ragionamento completo. Restituisce None (mai
        un'eccezione) se pywin32 manca o l'assegnazione fallisce per qualunque motivo - chi
        chiama tratta None esattamente come prima di questa correzione."""
        try:
            import win32api
            import win32con
            import win32job

            job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            handle = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid,
            )
            win32job.AssignProcessToJobObject(job, handle)
            return job
        except Exception:
            return None

    def _kill_and_abandon(self, process: subprocess.Popen, command: str, error: str, job=None) -> SkillResult:
        # Con shell=True su Windows, process e' cmd.exe: kill() lo termina subito, ma un
        # comando come "python -c ..." gira come NIPOTE (figlio di cmd.exe), non figlio diretto -
        # kill() da solo non lo tocca. Quando job non e' None (vedi _make_kill_tree_job),
        # win32job.TerminateJobObject termina anche i nipoti in un colpo solo; process.kill()
        # resta comunque come ripiego se il job non e' disponibile o la sua terminazione fallisce
        # (mai peggio del comportamento precedente). Riprodotto per davvero, due volte: la
        # prima versione di questo fix chiamava process.communicate() dopo kill() e restava
        # bloccata per l'intera durata del comando; sostituendola con process.wait() + chiusura
        # esplicita delle pipe restava ANCORA bloccata altrettanto a lungo - subprocess.communicate
        # (usato sopra con timeout, in un ciclo) avvia thread lettori in background che restano
        # bloccati in read() finche' la pipe non si chiude DAVVERO (cioe' finche' anche il nipote
        # orfano non termina); chiudere lo stream dal thread principale contende sullo stesso lock
        # interno del thread lettore e blocca allo stesso modo. Il solo modo per non aspettare il
        # nipote e' non toccare piu' le pipe dopo il kill: process.wait() aspetta solo che IL
        # PROCESSO UCCISO (cmd.exe) termini (veloce, indipendente dai suoi discendenti), le pipe
        # restano deliberatamente abbandonate ai thread lettori esistenti invece di essere chiuse
        # in modo sincrono - nessun output serve comunque quando il risultato e' KILLED/TIMEOUT.
        killed_via_job = False
        if job is not None:
            try:
                import win32job

                win32job.TerminateJobObject(job, 1)
                killed_via_job = True
            except Exception:
                killed_via_job = False
        if not killed_via_job:
            process.kill()
        process.wait(timeout=5)
        return SkillResult(success=False, data={"command": command}, error=error)

    def _build_result(self, command: str, return_code: int, stdout: str, stderr: str) -> SkillResult:
        output = (stdout or stderr or "").strip()
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "... (troncato)"

        # Riprodotto per davvero: prima di questa correzione un comando con un codice di uscita
        # diverso da zero (fallito per davvero: sintassi sbagliata, file non trovato, comando
        # inesistente...) veniva comunque riportato come success=True. L'utente se ne accorgeva
        # comunque leggendo "codice N" nella risposta (vedi core/response_formatter.py, il
        # messaggio resta identico), ma chi si fida del campo strutturato - il ledger di audit,
        # la dashboard (successi/fallimenti per skill), un futuro passo di PlanExecutor che
        # decidesse se proseguire in base a result.success - veniva ingannato.
        return SkillResult(
            success=return_code == 0,
            data={"command": command, "output": output, "return_code": return_code},
            error=None if return_code == 0 else "NONZERO_EXIT",
        )
