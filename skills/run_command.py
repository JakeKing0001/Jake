import subprocess

from core.command_safety import check_command_safety
from core.skill_result import SkillResult

MAX_OUTPUT_CHARS = 1500


class RunCommandSkill:
    """Esegue un comando da riga di comando qualsiasi (shell di sistema). E' la skill piu'
    potente e piu' rischiosa di Jake (puo' fare letteralmente qualsiasi cosa possa fare un
    comando da terminale): richiede sempre conferma esplicita col comando per esteso, cosi'
    l'utente puo' accorgersi subito di una trascrizione vocale sbagliata prima che venga eseguita."""

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

        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, timeout=30, text=True, encoding="utf-8", errors="ignore",
            )
        except subprocess.TimeoutExpired:
            return SkillResult(success=False, data={"command": command}, error="TIMEOUT")
        except Exception:
            return SkillResult(success=False, data={"command": command}, error="OPERATION_FAILED")

        output = (result.stdout or result.stderr or "").strip()
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
            success=result.returncode == 0,
            data={"command": command, "output": output, "return_code": result.returncode},
            error=None if result.returncode == 0 else "NONZERO_EXIT",
        )
