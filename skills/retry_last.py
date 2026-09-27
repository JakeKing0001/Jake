"""F4.6.2 ("retry sicuro"): "riprova" ripete l'ULTIMA azione fallita della conversazione corrente, attraverso
la stessa pipeline di un comando nuovo (policy, conferme, ricevute, undo). Sicuro vuol dire:
- solo un'azione fallita e recente, mai una riuscita (niente effetti doppi) ne' una frase non capita;
- i marcatori di conferma/autenticazione del tentativo precedente non valgono per il nuovo: la skill li
  richiede di nuovo;
- se l'intent non e' sicuro da ritentare (non READ_ONLY ne' idempotente, core/execution_safety.py) il primo
  tentativo potrebbe aver gia' prodotto una parte dell'effetto: Jake lo dice e chiede prima di riprovare."""
import time

from core.command import Command
from core.execution_safety import is_safe_to_auto_retry
from core.skill_result import SkillResult

RETRY_WINDOW_S = 15 * 60
CONFIRMATION_MARKERS = ("confirmed", "authenticated")
# frasi non capite o meta-comandi: non c'e' un'azione da ripetere
NOT_RETRYABLE = frozenset({
    "UNKNOWN", "CHITCHAT", "ASK_QUESTION", "HELP", "REPEAT_LAST", "CORRECT_LAST", "RETRY_LAST_ACTION",
    "UNDO_LAST_ACTION", "KILL_SWITCH", "RESET_KILL_SWITCH",
})


class RetryLastActionSkill:
    metadata = {
        "intent": "RETRY_LAST_ACTION",
        "description": (
            "Riprova l'ultima azione che e' fallita, con gli stessi parametri. Per 'riprova', 'prova di nuovo', "
            "'ritenta'. Non ripete un'azione riuscita."
        ),
        "parameters": {},
    }

    def __init__(self, core, clock=time.time):
        self.core = core
        self._clock = clock

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        runtime = self.core._get_dialogue_runtime()
        wanted = parameters.get("action_id")
        # dopo il "si'" si riprova esattamente l'azione mostrata nella domanda, non quella che nel frattempo e'
        # diventata l'ultima
        turn = runtime.turn(wanted) if isinstance(wanted, str) else runtime.last_turn(self.core._dialogue_scope())
        if turn is None or turn.status != "failed" or turn.intent in NOT_RETRYABLE:
            return SkillResult(success=False, data={}, error="NOTHING_TO_RETRY")
        if self._clock() - turn.at > RETRY_WINDOW_S:
            return SkillResult(success=False, data={"intent": turn.intent}, error="RETRY_EXPIRED")
        if not is_safe_to_auto_retry(turn.intent) and parameters.get("confirmed") is not True:
            return SkillResult(success=False, error="CONFIRMATION_REQUIRED", data={
                "message": (f"L'azione {turn.intent} non è andata a buon fine, ma potrebbe essere stata applicata in "
                            "parte. La riprovo?"),
                "confirm_parameters": {"action_id": turn.utterance_id, "confirmed": True},
            })
        retried = {k: v for k, v in turn.parameters.items() if k not in CONFIRMATION_MARKERS}
        response = self.core._execute_command(turn.heard, Command(turn.intent, retried), learn=False)
        return SkillResult(success=True, data={"response": response, "intent": turn.intent})
