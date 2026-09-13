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
esso stesso un consumatore di PolicyEngine (non ha bisogno di esserlo: non decide, propone).

F1.2.2 (capability, primo pezzo): `allowed_filesystem_roots` e' la prima capability vera - non
solo un intent permesso/vietato, ma UN INTENT permesso solo entro certi confini. Deliberatamente
opt-in e limitata: default vuoto (nessuna restrizione, comportamento identico a prima - Jake
continua a poter toccare qualunque percorso come sempre, non e' un cambio retroattivo che
romperebbe l'uso normale senza che l'utente lo chieda), applicata sia sul percorso interattivo
(decide_interactive) sia su quello automatico (decide_automated), e oggi copre sia le quattro
mutazioni (CREATE_PATH/RENAME_PATH/MOVE_PATH/DELETE_PATH) sia le tre letture
(FIND_FILE/GET_FILE_INFO/READ_FILE_TEXT, vedi FILESYSTEM_CAPABILITY_INTENTS piu' sotto per il
perche' OPEN_PATH ne resta fuori). App/contatto/dominio web/device/servizio Home Assistant/rete/
durata (le altre capability elencate in ROADMAP.md) e l'intersezione multi-livello di F1.2.3
(utente/dispositivo/agente/skill/sessione) restano completamente aperte."""
import os
from enum import Enum
from pathlib import Path


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


# F1.2.6 ("salvare la motivazione della decisione nel ledger senza salvare segreti"): un
# vocabolario CHIUSO, non testo libero - ne' _decide_*_reasoned() ne' chi le chiama puo' far
# finire un valore di parametro (potenzialmente un segreto: un token, un percorso privato, il
# contenuto di un messaggio) dentro la motivazione, perche' la motivazione e' sempre UNA di
# queste quattro costanti, mai una stringa costruita da `intent`/`parameters`. Questo e' cio'
# che rende "senza salvare segreti" vero per costruzione, non per convenzione.
POLICY_REASON_BLOCKED = "intent_in_blocked_intents"
POLICY_REASON_REQUIRE_AUTH = "intent_in_require_auth_intents_and_auth_gate_enabled"
POLICY_REASON_CONFIRM = "intent_in_always_confirm_intents"
POLICY_REASON_ALLOWED = "no_restriction_matched"
# F1.2.2: un percorso fuori da allowed_filesystem_roots - una motivazione DIVERSA da
# POLICY_REASON_BLOCKED (che significa "l'intent stesso e' bloccato sempre") perche' qui lo
# STESSO intent puo' essere permesso o negato a seconda del parametro, non dell'intent da solo.
POLICY_REASON_CAPABILITY_DENIED = "path_outside_allowed_filesystem_roots"
POLICY_REASONS = frozenset({
    POLICY_REASON_BLOCKED, POLICY_REASON_REQUIRE_AUTH, POLICY_REASON_CONFIRM, POLICY_REASON_ALLOWED,
    POLICY_REASON_CAPABILITY_DENIED,
})

# F1.2.2: le quattro mutazioni sono le stesse gia' raggruppate in core/execution_safety.py::
# INTENT_SAFETY_REGISTRY come "i quattro intent filesystem" (naturalmente idempotenti, con
# rollback) - quella e' pero' una lista con uno scopo DIVERSO (chi ha un compensating action per
# il rollback), una coincidenza di quattro nomi in comune, non lo stesso insieme per definizione:
# OPEN_PATH non c'e' qui (RiskLevel.LOCAL_REVERSIBLE, non READ_ONLY - apre un file con
# l'applicazione predefinita, un rischio diverso da una lettura pura, deliberatamente fuori da
# questa prima estensione). FIND_FILE/GET_FILE_INFO/READ_FILE_TEXT (RiskLevel.READ_ONLY, vedi
# core/risk.py) sono coperti da questa capability quanto le quattro mutazioni: "solo dentro le
# radici consentite" vale anche per trovare/leggere, non solo per creare/spostare/cancellare -
# altrimenti la capability lascerebbe comunque Jake libero di leggere qualunque file sul disco,
# vanificando in parte il senso di un "recinto" filesystem. FIND_FILE ha il suo parametro `path`
# OPZIONALE (cerca nelle cartelle utente comuni se omesso): quando omesso, questo controllo non ha
# nulla da confrontare e non si applica (stesso principio "un valore assente non viene bloccato"
# gia' vero per ogni altro intent qui) - un gap noto, non ancora chiuso: la ricerca di default
# nelle cartelle comuni puo' ancora uscire dalle radici consentite se l'utente non specifica un
# percorso esplicito.
FILESYSTEM_CAPABILITY_INTENTS = frozenset({
    "CREATE_PATH", "RENAME_PATH", "MOVE_PATH", "DELETE_PATH", "FIND_FILE", "GET_FILE_INFO", "READ_FILE_TEXT",
})

# Nomi di parametro gia' in uso dalle skill sopra per un percorso su cui l'azione ha effetto
# (vedi le rispettive metadata["parameters"]): "path" da tutte e sette, "destination" in aggiunta
# da MOVE_PATH - un file spostato FUORI dalle radici consentite sarebbe un modo per aggirare la
# capability anche partendo da un percorso permesso.
_FILESYSTEM_PATH_PARAMETER_KEYS = ("path", "destination")


def _normalized_for_comparison(path: Path) -> str:
    # os.path.normcase abbassa il case su Windows (NTFS e' case-insensitive per default) e non
    # fa nulla su POSIX - stessa normalizzazione su entrambi i lati del confronto.
    return os.path.normcase(str(path))


def _is_within_root(path: Path, root: Path) -> bool:
    """True se `path` e' uguale a `root` o un suo discendente. Confronto per stringa (non
    Path.is_relative_to, che su Windows non normalizza il case) dopo normcase su entrambi i lati;
    il separatore esplicito nel confronto evita che una radice "C:\\Allowed" corrisponda per
    errore a un percorso "C:\\AllowedButNot" (stesso prefisso di stringa, cartella diversa)."""
    path_str, root_str = _normalized_for_comparison(path), _normalized_for_comparison(root)
    return path_str == root_str or path_str.startswith(root_str + os.sep)


class PolicyEngine:
    """Un'istanza per JakeCore, condivisa PER RIFERIMENTO (non copiata) con tutto cio' che deve
    decidere se un intent puo' eseguire: JakeCore stesso, PlanExecutor (tramite `execute()`),
    TriggerScheduler, RunWorkflowSkill. Un solo oggetto da collegare invece di tre insiemi
    passati a mano in punti diversi (vedi il docstring del modulo)."""

    def __init__(
        self, auth_gate=None, blocked_intents: set | None = None, always_confirm_intents: set | None = None,
        require_auth_intents: set | None = None, allowed_filesystem_roots: set | list | None = None,
    ):
        self.auth_gate = auth_gate
        self.blocked_intents = set(blocked_intents or set())
        self.always_confirm_intents = set(always_confirm_intents or set())
        self.require_auth_intents = set(require_auth_intents or set())
        # F1.2.2: vuoto/None (default) = nessuna restrizione, comportamento invariato rispetto a
        # prima che questa capability esistesse - stesso principio di blocked_intents/
        # always_confirm_intents, gia' vuoti per default. Risolti una volta qui (non a ogni
        # controllo): un percorso configurato che non esiste ancora sul disco si risolve comunque
        # in modo deterministico con Path.resolve() (non richiede che esista).
        self._allowed_filesystem_roots = [Path(root).resolve() for root in (allowed_filesystem_roots or [])]

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
        return self._decide_interactive_reasoned(intent, parameters)[0]

    def decide_automated(self, intent: str, parameters: dict | None = None) -> PolicyDecision:
        """Percorso automatico: PlanExecutor (il ripiego del planner, RUN_WORKFLOW, i trigger).
        NESSUNO e' pronto a confermare/autenticare in tempo reale qui, quindi:
        - non esiste un gradino REQUIRE_AUTH separato: un intent ADMIN e' comunque gia' in
          always_confirm_intents (needs_central_confirmation include ADMIN, vedi core/risk.py),
          quindi si ferma comunque con CONFIRM - REQUIRE_AUTH ha senso solo quando c'e' un
          utente a cui chiedere la passphrase;
        - chi chiama DEVE aver gia' ripulito i parametri del passo con
          strip_authorization_signals() prima di passarli qui: questo metodo ignora comunque le
          chiavi di autorizzazione (vedi AUTHORIZATION_SIGNAL_KEYS), cosi' un "confirmed": true
          falsificato non ha nessun modo di influenzare il risultato, ma DA F1.2.2 guarda
          `parameters` per la capability filesystem (allowed_filesystem_roots) - lo stesso
          controllo gia' applicato al percorso interattivo, ora anche qui: un piano/automazione/
          trigger non puo' piu' mutare un percorso fuori dalle radici consentite solo perche' non
          c'e' un utente a confermare. `parameters=None` (default) equivale a nessun parametro:
          comportamento invariato per chi non ne passa (nessuna restrizione applicabile senza un
          percorso da controllare)."""
        return self._decide_automated_reasoned(intent, parameters)[0]

    # ---- F1.2.7 (policy simulator): "mostra se e perche' un'azione sarebbe permessa" ---------
    #
    # Le due varianti "_reasoned" sotto sono l'UNICA fonte della logica di decisione: decide_
    # interactive()/decide_automated() sopra ne scartano solo il motivo, invece di duplicare gli
    # stessi if/elif in una seconda copia - esattamente il pattern di bug (due strutture quasi
    # identiche che divergono in silenzio) gia' documentato nel docstring del modulo per
    # RunWorkflowSkill. explain() le chiama entrambe e le espone insieme, per capire in anticipo
    # (senza eseguire nulla) cosa succederebbe a un intent sia da un comando diretto sia da
    # un'automazione - i due percorsi possono dare esiti diversi (REQUIRE_AUTH esiste solo per
    # quello interattivo).

    def _decide_interactive_reasoned(self, intent: str, parameters: dict | None) -> tuple[PolicyDecision, str]:
        parameters = parameters or {}
        if intent in self.blocked_intents:
            return PolicyDecision.BLOCK, POLICY_REASON_BLOCKED
        if intent in FILESYSTEM_CAPABILITY_INTENTS and not self._filesystem_capability_allows(parameters):
            return PolicyDecision.BLOCK, POLICY_REASON_CAPABILITY_DENIED
        if (
            self.auth_gate is not None
            and getattr(self.auth_gate, "enabled", False)
            and intent in self.require_auth_intents
            and not parameters.get("authenticated")
        ):
            return PolicyDecision.REQUIRE_AUTH, POLICY_REASON_REQUIRE_AUTH
        if intent in self.always_confirm_intents and not parameters.get("confirmed"):
            return PolicyDecision.CONFIRM, POLICY_REASON_CONFIRM
        return PolicyDecision.ALLOW, POLICY_REASON_ALLOWED

    def _decide_automated_reasoned(self, intent: str, parameters: dict | None = None) -> tuple[PolicyDecision, str]:
        # F1.2.2 (seconda fetta): allowed_filesystem_roots ora applicato anche qui, con lo stesso
        # ordine di priorita' del percorso interattivo (blocked_intents vince su tutto, la
        # capability viene controllata PRIMA di CONFIRM - un'automazione con un DELETE_PATH fuori
        # dalle radici consentite deve fermarsi con BLOCK/CAPABILITY_DENIED, non arrivare a
        # CONFIRM che qui non ha comunque nessuno pronto a rispondere). L'unico chiamante di
        # produzione (PlanExecutor.execute()) passa gia' `safe_parameters` - i parametri del passo
        # DOPO strip_authorization_signals() - quindi non serve ripeterlo qui: le uniche chiavi
        # che questo controllo legge sono `path`/`destination` (vedi
        # _FILESYSTEM_PATH_PARAMETER_KEYS), mai le chiavi di autorizzazione.
        if intent in self.blocked_intents:
            return PolicyDecision.BLOCK, POLICY_REASON_BLOCKED
        if intent in FILESYSTEM_CAPABILITY_INTENTS and not self._filesystem_capability_allows(parameters or {}):
            return PolicyDecision.BLOCK, POLICY_REASON_CAPABILITY_DENIED
        if intent in self.always_confirm_intents:
            return PolicyDecision.CONFIRM, POLICY_REASON_CONFIRM
        return PolicyDecision.ALLOW, POLICY_REASON_ALLOWED

    def _filesystem_capability_allows(self, parameters: dict) -> bool:
        """True se nessuna radice e' configurata (default, nessuna restrizione) oppure se OGNI
        parametro-percorso presente (`path`, e `destination` per MOVE_PATH) e' entro una delle
        radici consentite. Un percorso che non si riesce nemmeno a risolvere (caratteri non
        validi, troppo lungo) non viene approvato per difetto - vedi validate_action_receipt e lo
        stesso principio "nega per default" gia' applicato altrove in F1.2."""
        if not self._allowed_filesystem_roots:
            return True
        for key in _FILESYSTEM_PATH_PARAMETER_KEYS:
            value = parameters.get(key)
            if not value or not isinstance(value, str):
                continue
            try:
                resolved = Path(value).resolve()
            except (OSError, ValueError):
                return False
            if not any(_is_within_root(resolved, root) for root in self._allowed_filesystem_roots):
                return False
        return True

    # F1.2.6: varianti PUBBLICHE delle due sopra, per chi (PlanExecutor, F1.2.6) ha bisogno di
    # salvare la motivazione nel ledger insieme alla decisione, senza ricalcolarla una seconda
    # volta con explain() (che calcola anche il verdetto dell'altro percorso, inutile qui) ne'
    # accedere a un metodo "privato" da fuori il modulo.
    def decide_interactive_with_reason(self, intent: str, parameters: dict | None) -> tuple[PolicyDecision, str]:
        return self._decide_interactive_reasoned(intent, parameters)

    def decide_automated_with_reason(self, intent: str, parameters: dict | None = None) -> tuple[PolicyDecision, str]:
        return self._decide_automated_reasoned(intent, parameters)

    def explain(self, intent: str, parameters: dict | None = None) -> dict:
        """Simula la policy per `intent` SENZA eseguire nulla: utile per un pannello diagnostico
        (HUD/companion, non ancora costruito) o per debug locale - "perche' Jake mi ha chiesto
        conferma per X?"/"questa automazione si fermerebbe?". Le chiavi di autorizzazione
        (`confirmed`/`authenticated`) contano solo per il verdetto interattivo, coerenti con
        decide_interactive: il verdetto automatico le ignora sempre, per lo stesso motivo per cui
        decide_automated() le ignora (nessun segnale di autorizzazione e' mai genuino in un
        percorso automatico - vedi strip_authorization_signals). DA F1.2.2 (seconda fetta) i
        parametri-percorso (`path`/`destination`) contano invece per ENTRAMBI i verdetti: la
        capability filesystem si applica sia al percorso interattivo sia a quello automatico.
        Restituisce entrambi i verdetti perche' possono differire: REQUIRE_AUTH esiste solo per
        il percorso interattivo."""
        interactive_decision, interactive_reason = self._decide_interactive_reasoned(intent, parameters)
        automated_decision, automated_reason = self._decide_automated_reasoned(intent, parameters)
        return {
            "intent": intent,
            "interactive": {"decision": interactive_decision.value, "reason": interactive_reason},
            "automated": {"decision": automated_decision.value, "reason": automated_reason},
        }
