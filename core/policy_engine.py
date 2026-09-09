"""Motore di policy centralizzato (F1, Trustworthy Agent Core 3.0 - "separazione formale
planner/policy engine/executor" in ROADMAP.md, fase F1).

Prima di questo modulo, la stessa domanda - "questo intent e' bloccato? richiede conferma o
autenticazione?" - veniva risposta con logica scritta a mano in due posti diversi:
JakeCore._resolve_and_execute (percorso interattivo: comando singolo E, tramite l'`executor`
passato a TaskAgent, anche l'agente a passi generale/coding/ricerca) e PlanExecutor.execute
(piani automatici: il ripiego del planner, RUN_WORKFLOW, i trigger). Le due implementazioni
erano quasi identiche ma non uguali, e "quasi identiche" e' proprio il problema: due bug reali
trovati verificando per davvero (non leggendo il codice a tavolino) sono nati esattamente da
questo disallineamento:

1. PlanExecutor.execute() non toglieva mai confirmed/authenticated dai parametri di un passo:
   un piano automatico poteva "auto-autorizzarsi" se quelle chiavi arrivavano gia' impostate
   (un planner/LLM indotto da un prompt costruito ad arte, o un workflow salvato manomesso).
2. JakeCore._resolve_and_execute() non controllava MAI blocked_intents (solo
   JakeCore._execute_command lo faceva, PRIMA di chiamarla): un intent che l'utente ha
   esplicitamente disabilitato in config.json restava comunque eseguibile dall'agente a passi
   (generale, coding, ricerca), che passa da _resolve_and_execute e non da _execute_command.

Questo modulo non introduce nuova policy: sposta quella gia' esistente (risk_of()/
needs_central_confirmation()/needs_central_auth() da core/risk.py, blocked_intents/
always_confirm_intents/require_auth_intents da config e dal censimento del rischio) in un solo
posto testato una volta sola, usato da entrambi i percorsi - cosi' un controllo aggiunto o
corretto qui vale per tutti e due insieme, invece di dover essere ricordato due volte.

Non e' ancora un vero "kernel dei permessi" con capability token per skill/agente/dispositivo
(quello resta un pezzo piu' grande, dichiarato ⬜ in ROADMAP.md): e' il primo passo - centralizzare
la decisione - fatto per davvero, non solo etichettato."""
from enum import Enum


class PolicyDecision(str, Enum):
    """Le stesse quattro risposte del futuro Permissions & Security Kernel (fase 5.4/F1):
    ALLOW/CONFIRM/REQUIRE_AUTH/BLOCK."""

    ALLOW = "allow"
    CONFIRM = "confirm"
    REQUIRE_AUTH = "require_auth"
    BLOCK = "block"


# Chiavi che segnalano un'autorizzazione gia' concessa: hanno senso solo quando le imposta
# DAVVERO un gate interattivo, nello stesso turno in cui l'utente ha appena detto si'/la
# passphrase/verificato Windows Hello (vedi decide_interactive). Un percorso automatico (vedi
# decide_automated) non ha mai nessuno pronto a concederle in tempo reale: se le trova gia' nei
# parametri di un passo, sono un segnale falsificato (da un LLM indotto da un prompt costruito ad
# arte, o da un file manomesso), non una prova - vedi strip_authorization_signals.
AUTHORIZATION_SIGNAL_KEYS = ("confirmed", "authenticated", "authenticated_via")


def strip_authorization_signals(parameters: dict) -> dict:
    """Toglie le chiavi di autorizzazione da un dict di parametri, per il percorso automatico
    (PlanExecutor): un piano non puo' mai auto-autorizzarsi. Non muta l'originale."""
    return {key: value for key, value in (parameters or {}).items() if key not in AUTHORIZATION_SIGNAL_KEYS}


def register_intent(intent: str, *, always_confirm_intents: set, require_auth_intents: set) -> None:
    """Sincronizza UN intent con la policy corrente, secondo la sua classificazione del rischio
    (core/risk.py). Stessa funzione usata sia per popolare i due insiemi all'avvio
    (JakeCore.__init__, un intent alla volta) sia quando una skill viene installata a runtime
    dalla Skill Forge (JakeCore._on_skill_installed): prima di questo modulo erano due copie
    della stessa logica scritte a mano, con il rischio concreto (gia' successo una volta, vedi
    ROADMAP.md F1) che una delle due si dimenticasse un pezzo."""
    from core.risk import needs_central_auth, needs_central_confirmation

    if needs_central_confirmation(intent):
        always_confirm_intents.add(intent)
    if needs_central_auth(intent):
        require_auth_intents.add(intent)


def decide_interactive(
    intent: str,
    parameters: dict,
    *,
    blocked_intents: set = None,
    always_confirm_intents: set = None,
    require_auth_intents: set = None,
    auth_gate=None,
) -> PolicyDecision:
    """Percorso interattivo: comando singolo (JakeCore._execute_command) E agente a passi
    (TaskAgent, tramite l'`executor` che richiama JakeCore._resolve_and_execute) - un vero
    si'/passphrase/Windows Hello PUO' arrivare nei parametri nello STESSO turno, messo li'
    davvero da JakeCore dopo che l'utente ha risposto: qui va rispettato, a differenza del
    percorso automatico (vedi decide_automated).

    blocked_intents e' controllato qui - non solo a monte in _execute_command - proprio perche'
    l'agente a passi non passa MAI da _execute_command: prima di questo modulo un intent
    disabilitato dall'utente in config.json restava eseguibile da un compito composto."""
    parameters = parameters or {}
    if intent in (blocked_intents or ()):
        return PolicyDecision.BLOCK
    if (
        auth_gate is not None
        and getattr(auth_gate, "enabled", False)
        and intent in (require_auth_intents or ())
        and not parameters.get("authenticated")
    ):
        return PolicyDecision.REQUIRE_AUTH
    if intent in (always_confirm_intents or ()) and not parameters.get("confirmed"):
        return PolicyDecision.CONFIRM
    return PolicyDecision.ALLOW


def decide_automated(
    intent: str, *, blocked_intents: set = None, always_confirm_intents: set = None,
) -> PolicyDecision:
    """Percorso automatico: PlanExecutor (il ripiego del planner, RUN_WORKFLOW, i trigger).
    NESSUNO e' pronto a confermare/autenticare in tempo reale qui, quindi:
    - non esiste un gradino REQUIRE_AUTH separato: un intent ADMIN e' comunque gia' in
      always_confirm_intents (needs_central_confirmation include ADMIN, vedi core/risk.py),
      quindi si ferma comunque con CONFIRM - REQUIRE_AUTH ha senso solo quando c'e' un utente a
      cui chiedere la passphrase;
    - chi chiama DEVE aver gia' ripulito i parametri del passo con
      strip_authorization_signals() prima di eseguirlo: questa funzione non guarda i parametri
      del tutto, decide solo in base all'intent, cosi' un "confirmed": true falsificato non ha
      nessun modo di influenzare il risultato."""
    if intent in (blocked_intents or ()):
        return PolicyDecision.BLOCK
    if intent in (always_confirm_intents or ()):
        return PolicyDecision.CONFIRM
    return PolicyDecision.ALLOW
