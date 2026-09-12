import subprocess

from core.command_safety import check_command_safety
from core.skill_result import SkillResult

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
    Limite noto e dichiarato, non risolto qui: `process.kill()` termina solo il processo della
    shell (`shell=True`), non un eventuale albero di sotto-processi che il comando avesse
    lanciato - un kill dell'intero process tree richiede un Job Object (F1.6.3, sandbox
    permanente per skill forgiate), un lavoro piu' ampio rimandato deliberatamente."""

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

        if self.kill_switch is None:
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

        elapsed = 0.0
        while True:
            try:
                stdout, stderr = process.communicate(timeout=KILL_SWITCH_POLL_SECONDS)
                return self._build_result(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if self.kill_switch.is_active():
                    return self._kill_and_abandon(process, command, "KILLED")
                elapsed += KILL_SWITCH_POLL_SECONDS
                if elapsed >= COMMAND_TIMEOUT_SECONDS:
                    return self._kill_and_abandon(process, command, "TIMEOUT")

    def _kill_and_abandon(self, process: subprocess.Popen, command: str, error: str) -> SkillResult:
        # Con shell=True su Windows, process e' cmd.exe: kill() lo termina subito, ma un
        # comando come "python -c ..." gira come NIPOTE (figlio di cmd.exe), non figlio diretto -
        # kill() non lo tocca (limite noto, vedi il docstring della classe: un kill dell'intero
        # process tree richiede un Job Object, F1.6.3). Riprodotto per davvero, due volte: la
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
