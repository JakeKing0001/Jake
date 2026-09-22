"""Annulla l'ultima azione (F1.3.5, "generare undo token con scadenza e precondizioni" - vedi
ROADMAP_EXECUTION.md sezione F1.3): il consumatore che rende finalmente visibile all'utente il
meccanismo costruito in core/undo_store.py e adottato su tutti e tre i chokepoint reali
(JakeCore/TaskAgent/PlanExecutor, PR #135-#138) - fino a questo modulo lo store esisteva solo nei
test, popolato ma mai letto da nessuna parte."""
from core.skill_result import SkillResult

# Un frammento leggibile per ciascun intent compensatorio gia' noto (core/execution_safety.py::
# UNDO_PARAMS_BY_INTENT) - non un elenco esaustivo di ogni intent possibile, solo di quelli gia'
# supportati da generate_undo_descriptor() oggi. Un compensating_intent futuro non ancora in
# questa mappa ricade sul fallback onesto sotto, mai un crash. CANCEL_TIMER/STOP_POMODORO/
# TOGGLE_DARK_MODE compaiono anche come intent ORIGINALE possibile (non solo compensatorio) - qui
# contano SOLO come intent compensatorio in uscita da questa mappa, nessuna ambiguita': la chiave
# e' sempre descriptor.compensating_intent.
_UNDO_DESCRIPTIONS = {
    "DELETE_PATH": lambda params: f"eliminare {params.get('path', '?')}",
    "MOVE_PATH": lambda params: f"spostare {params.get('path', '?')} in {params.get('destination', '?')}",
    "RENAME_PATH": lambda params: f"rinominare {params.get('path', '?')} in {params.get('new_name', '?')}",
    "DELETE_TODO": lambda params: f"togliere '{params.get('text', '?')}' dalla lista delle cose da fare",
    "DELETE_REMINDER": lambda params: f"cancellare il promemoria '{params.get('text', '?')}'",
    "CANCEL_TIMER": lambda params: "annullare il timer" + (f" '{params['label']}'" if params.get("label") else ""),
    "STOP_POMODORO": lambda params: "fermare la sessione pomodoro",
    "RESTORE_WINDOW": lambda params: f"ripristinare la finestra '{params.get('title', '?')}'",
    "TOGGLE_DARK_MODE": lambda params: "attivare il tema scuro" if params.get("enabled") else "disattivare il tema scuro",
}


def _describe_undo(compensating_intent: str, compensating_parameters: dict) -> str:
    describe = _UNDO_DESCRIPTIONS.get(compensating_intent)
    if describe is not None:
        return describe(compensating_parameters)
    return f"eseguire {compensating_intent}"


class UndoLastActionSkill:
    """Non esegue MAI direttamente l'azione compensatoria: propone SOLO una busta
    CONFIRMATION_REQUIRED con l'intent/parametri gia' calcolati da generate_undo_descriptor()
    (execution_safety.UNDO_PARAMS_BY_INTENT) - la conferma dell'utente passa dalla STESSA
    pipeline di policy/esecuzione/verifica di qualunque altro comando
    (JakeCore._finalize_pending_action), mai un'esecuzione diretta qui che bypasserebbe
    PolicyEngine/blocked_intents per l'intent compensatorio (spesso DESTRUCTIVE - es. DELETE_PATH
    per annullare un CREATE_PATH). compensating_parameters porta gia' 'confirmed': True quando la
    skill compensatoria lo richiede (stesso calcolo gia' usato per la compensazione automatica su
    verifica fallita, F1.3.3): un solo giro di conferma per l'intero undo, non due.

    Limite dichiarato apertamente: non chiama mai UndoStore.mark_used() (ne' qui ne' altrove) -
    un utente potrebbe in teoria chiedere lo stesso undo due volte prima che scada. Non un buco di
    sicurezza (l'intent compensatorio e' idempotente per costruzione, es. DELETE_PATH su un
    percorso gia' cancellato torna PATH_NOT_FOUND, non un secondo danno), solo una rifinitura UX
    rimandata: marcare l'undo consumato richiederebbe un callback DOPO che la conferma e' stata
    eseguita per davvero, che la pipeline di conferma generica non supporta ancora."""

    metadata = {
        "intent": "UNDO_LAST_ACTION",
        "description": "Annulla l'ultima azione riuscita che puo' essere annullata (creare/spostare/rinominare/"
        "duplicare un file o una cartella, estrarre un archivio, uno screenshot, una nota da fare, un promemoria, "
        "un timer, una sessione pomodoro, minimizzare/massimizzare una finestra, il tema scuro) - non ogni azione "
        "ha un inverso naturale, e un undo scade dopo qualche minuto. Usalo per 'annulla', 'annulla l'ultima "
        "azione', 'disfa', 'torna indietro', 'annulla quello che hai appena fatto'.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        descriptor = self.core.undo_store.most_recent_usable()
        if descriptor is None:
            return SkillResult(success=False, data={}, error="NO_UNDO_AVAILABLE")
        description = _describe_undo(descriptor.compensating_intent, descriptor.compensating_parameters)
        return SkillResult(
            success=False,
            data={
                "message": f"Vuoi annullare l'ultima azione? Sto per {description}.",
                "confirm_intent": descriptor.compensating_intent,
                "confirm_parameters": dict(descriptor.compensating_parameters),
            },
            error="CONFIRMATION_REQUIRED",
        )
