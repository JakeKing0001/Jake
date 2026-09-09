"""Motore di policy centralizzato (F1, Trustworthy Agent Core 3.0 - "separazione formale
planner/policy engine/executor" in ROADMAP.md, fase F1).

Prima di questo modulo, la stessa domanda - "questo intent e' bloccato? richiede conferma o
autenticazione?" - veniva risposta con logica scritta a mano in due posti diversi:
JakeCore._resolve_and_execute (percorso interattivo: comando singolo E, tramite l'`executor`
passato a TaskAgent, anche l'agente a passi generale/coding/ricerca) e PlanExecutor.execute
(piani automatici: il ripiego del planner, RUN_WORKFLOW, i trigger). Le due implementazioni
erano quasi identiche ma non uguali, e "quasi identiche" e' proprio il problema: bug reali
trovati verificando per davvero (non leggendo il codice a tavolino) sono nati esattamente da
questo disallineamento:

1. PlanExecutor.execute() non toglieva mai confirmed/authenticated dai parametri di un passo:
   un piano automatico poteva "auto-autorizzarsi" se quelle chiavi arrivavano gia' impostate.
2. JakeCore._resolve_and_execute() non controllava MAI blocked_intents: un intent che l'utente
   ha esplicitamente disabilitato in config.json restava comunque eseguibile dall'agente a passi.
3. RunWorkflowSkill non passava affatto blocked_intents/always_confirm_intents a
   PlanExecutor.execute(): un'automazione con un passo DESTRUCTIVE/ADMIN eseguiva senza conferma.

Il terzo bug in particolare e' nato da un dettaglio strutturale specifico: blocked_intents e
always_confirm_intents erano DUE riferimenti separati che ogni nuovo consumatore doveva
ricordarsi di collegare entrambi (vedi JakeCore.__init__: `run_workflow_skill.blocked_intents =
...` E `run_workflow_skill.always_confirm_intents = ...`, due righe, non una). PolicyEngine
sposta quello stato in UN oggetto: chi ha bisogno della policy riceve un riferimento solo,
`self.policy_engine`, non piu' due insiemi paralleli che possono essere collegati a meta'.

Non e' ancora un vero "kernel dei permessi" con capability token per skill/agente/dispositivo
(quello resta un pezzo piu' grande, dichiarato ⬜ in ROADMAP.md): e' la separazione
planner/policy/executor completata per la parte policy/executor - il planner
(core/planner_provider.py) resta un produttore di piani che la policy poi giudica, non ancora
esso stesso un consumatore di PolicyEngine (non ha bisogno di esserlo: non decide, propone)."""
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
# passphrase/verificato Windows Hello (vedi PolicyEngine.decide_interactive). Un percorso
# automatico (vedi decide_automated) non ha mai nessuno pronto a concederle in tempo reale: se
# le trova gia' nei parametri di un passo, sono un segnale falsificato (da un LLM indotto da un
# prompt costruito ad arte, o da un file manomesso), non una prova - vedi
# strip_authorization_signals.
AUTHORIZATION_SIGNAL_KEYS = ("confirmed", "authenticated", "authenticated_via")


def strip_authorization_signals(parameters: dict) -> dict:
    """Toglie le chiavi di autorizzazione da un dict di parametri, per il percorso automatico
    (PlanExecutor): un piano non puo' mai auto-autorizzarsi. Non muta l'originale. Funzione pura
    (non ha bisogno di stato di policy), a differenza del resto del modulo."""
    return {key: value for key, value in (parameters or {}).items() if key not in AUTHORIZATION_SIGNAL_KEYS}


class PolicyEngine:
    """Un'istanza per JakeCore, condivisa PER RIFERIMENTO (non copiata) con tutto cio' che deve
    decidere se un intent puo' eseguire: JakeCore stesso, PlanExecutor (tramite `execute()`),
    TriggerScheduler, RunWorkflowSkill. Un solo oggetto da collegare invece di tre insiemi
    passati a mano in punti diversi (vedi il docstring del modulo)."""

    def __init__(
        self, auth_gate=None, blocked_intents: set = None, always_confirm_intents: set = None,
        require_auth_intents: set = None,
    ):
        self.auth_gate = auth_gate
        self.blocked_intents = set(blocked_intents or set())
        self.always_confirm_intents = set(always_confirm_intents or set())
        self.require_auth_intents = set(require_auth_intents or set())

    def register_intent(self, intent: str) -> None:
        """Sincronizza UN intent con la policy corrente, secondo la sua classificazione del
        rischio (core/risk.py). Stessa funzione usata sia per popolare gli insiemi all'avvio
        (JakeCore.__init__, un intent alla volta via sync_with_registry) sia quando una skill
        viene installata a runtime dalla Skill Forge (JakeCore._on_skill_installed): un solo
        posto invece di due copie della stessa logica scritte a mano, con il rischio concreto
        (gia' successo una volta, vedi ROADMAP.md F1) che una delle due si dimenticasse un
        pezzo."""
        from core.risk import needs_central_auth, needs_central_confirmation

        if needs_central_confirmation(intent):
            self.always_confirm_intents.add(intent)
        if needs_central_auth(intent):
            self.require_auth_intents.add(intent)

    def sync_with_registry(self, skill_registry) -> None:
        """Registra ogni skill gia' nota al registry (chiamato una volta, dopo che tutte le
        skill built-in/plugin sono state caricate - vedi JakeCore.__init__)."""
        for intent in skill_registry.skills:
            self.register_intent(intent)

    def decide_interactive(self, intent: str, parameters: dict) -> PolicyDecision:
        """Percorso interattivo: comando singolo (JakeCore._execute_command) E agente a passi
        (TaskAgent, tramite l'`executor` che richiama JakeCore._resolve_and_execute) - un vero
        si'/passphrase/Windows Hello PUO' arrivare nei parametri nello STESSO turno, messo li'
        davvero da JakeCore dopo che l'utente ha risposto: qui va rispettato, a differenza del
        percorso automatico (vedi decide_automated).

        blocked_intents e' controllato qui - non solo a monte in _execute_command - proprio
        perche' l'agente a passi non passa MAI da _execute_command: prima di questo modulo un
        intent disabilitato dall'utente in config.json restava eseguibile da un compito
        composto."""
        parameters = parameters or {}
        if intent in self.blocked_intents:
            return PolicyDecision.BLOCK
        if (
            self.auth_gate is not None
            and getattr(self.auth_gate, "enabled", False)
            and intent in self.require_auth_intents
            and not parameters.get("authenticated")
        ):
            return PolicyDecision.REQUIRE_AUTH
        if intent in self.always_confirm_intents and not parameters.get("confirmed"):
            return PolicyDecision.CONFIRM
        return PolicyDecision.ALLOW

    def decide_automated(self, intent: str) -> PolicyDecision:
        """Percorso automatico: PlanExecutor (il ripiego del planner, RUN_WORKFLOW, i trigger).
        NESSUNO e' pronto a confermare/autenticare in tempo reale qui, quindi:
        - non esiste un gradino REQUIRE_AUTH separato: un intent ADMIN e' comunque gia' in
          always_confirm_intents (needs_central_confirmation include ADMIN, vedi core/risk.py),
          quindi si ferma comunque con CONFIRM - REQUIRE_AUTH ha senso solo quando c'e' un
          utente a cui chiedere la passphrase;
        - chi chiama DEVE aver gia' ripulito i parametri del passo con
          strip_authorization_signals() prima di eseguirlo: questo metodo non guarda i
          parametri del tutto, decide solo in base all'intent, cosi' un "confirmed": true
          falsificato non ha nessun modo di influenzare il risultato."""
        if intent in self.blocked_intents:
            return PolicyDecision.BLOCK
        if intent in self.always_confirm_intents:
            return PolicyDecision.CONFIRM
        return PolicyDecision.ALLOW
