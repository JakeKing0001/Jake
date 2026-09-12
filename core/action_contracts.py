"""Contratti condivisi del percorso azione (F1.1.2, Action Contract 2.0 - vedi la fase F1 in
ROADMAP_EXECUTION.md).

Prima di questo modulo, l'unico contratto formale esistente era `ActionReceipt`
(core/action_ledger.py): quello che un'azione HA GIA' PRODOTTO. Mancavano i contratti per le fasi
PRIMA e ATTORNO all'esecuzione - cosa un agente/planner/skill PROPONE di fare (`ActionProposal`),
in quale contesto (`ActionContext`), come si dimostra che l'effetto e' avvenuto davvero
(`VerificationEvidence`), come si annulla (`UndoDescriptor`), e come si descrive un fallimento in
modo strutturato invece di una stringa grezza (`ActionError`).

**Cosa questo modulo NON fa (dichiarato esplicitamente, non lasciato intuire)**: non collega
ancora questi tipi ai quattro chokepoint reali che orchestrano un'azione
(`JakeCore._execute_command`/`_finalize_pending_action`, `TaskAgent.run`, `PlanExecutor.execute`),
ne' alle ~200 skill (che continuano a restituire `SkillResult`, piu' sottile - vedi
core/skill_result.py). Quella migrazione, skill per skill e chokepoint per chokepoint, e' F1.1.6
("adattare prima un intent read-only, uno reversibile, uno external, uno destructive e uno
admin") e F1.1.7 ("migrare tutti gli intent per dominio"): un progetto a se', volutamente
successivo a questo - introdurre 5 contratti nuovi e riscrivere ~200 skill nello stesso commit
sarebbe impossibile da rivedere e da annullare come un'unica unita' logica (vedi la Definition of
Done in ROADMAP_EXECUTION.md, punto 8). Questo modulo esiste perche' F1.1.2 e F1.1.6/F1.1.7 sono
passi NUMERATI SEPARATAMENTE nella roadmap proprio per questo motivo: prima il contratto, poi
l'adozione.

Ogni tipo qui riusa le costanti/enum gia' esistenti altrove invece di introdurne una copia
parallela (lo stesso principio gia' applicato in F1.1.4/F1.3.3): `VerificationEvidence.status`
usa le tre costanti `VERIFICATION_*` di core/action_ledger.py, `ActionError.category` usa le
costanti `ERROR_CATEGORY_*` dello stesso modulo, `ActionProposal.risk` usa core/risk.py::RiskLevel."""
import time
from dataclasses import dataclass
from typing import Optional

from core.action_ledger import (
    ERROR_CATEGORIES, VERIFICATION_FAILED, VERIFICATION_UNVERIFIED, VERIFICATION_VERIFIED,
    error_category_of, normalize_result_code,
)
from core.execution_safety import RETRYABLE_ERRORS
from core.risk import RiskLevel, risk_of

_VERIFICATION_STATUSES = (VERIFICATION_VERIFIED, VERIFICATION_UNVERIFIED, VERIFICATION_FAILED)

# F1.1.2: solo cinque valori, non un'enumerazione libera - un ActionProposal che non sa dire in
# quale di questi cade il proprio effetto non ha ancora abbastanza informazione per essere
# giudicato dal PolicyEngine (F1.2.2 lo user' per le capability, quando esistera'). A differenza
# di RiskLevel (core/risk.py, "quanto e' grave se va male"), effect_class descrive IL TIPO di
# effetto ("cosa cambia nel mondo"): un intent READ_ONLY e' sempre "read", ma un intent
# LOCAL_REVERSIBLE puo' essere "create" (ADD_NOTE), "modify" (RENAME_PATH) o "delete" (DELETE_PATH
# e' invece DESTRUCTIVE) - i due assi non si derivano l'uno dall'altro in modo affidabile per le
# ~200 skill non ancora censite, quindi qui resta un campo dichiarato dal chiamante, non
# calcolato automaticamente da risk_of() (che risponderebbe alla domanda sbagliata).
EFFECT_CLASS_READ = "read"
EFFECT_CLASS_CREATE = "create"
EFFECT_CLASS_MODIFY = "modify"
EFFECT_CLASS_DELETE = "delete"
EFFECT_CLASS_EXTERNAL = "external"
EFFECT_CLASSES = frozenset({
    EFFECT_CLASS_READ, EFFECT_CLASS_CREATE, EFFECT_CLASS_MODIFY, EFFECT_CLASS_DELETE,
    EFFECT_CLASS_EXTERNAL,
})


@dataclass
class ActionProposal:
    """Cio' che un agente/planner/skill PROPONE di fare, prima che il PolicyEngine decida (F1.1.2:
    "intent, parametri tipizzati, rischio, scope, precondizioni, effetto atteso" - vedi la tabella
    dei contratti condivisi in ROADMAP_EXECUTION.md, sezione 4.2). Oggi questa informazione esiste
    solo in modo implicito e sparso (l'`intent`/`parameters` passati a `PolicyEngine.
    decide_interactive`/`decide_automated`, il rischio ricavato al volo da `risk_of()`): niente la
    raggruppa in un unico oggetto ispezionabile PRIMA dell'esecuzione. `effect_class` e
    `preconditions`/`expected_effect` sono facoltativi e dichiarati dal chiamante (non derivabili
    in modo affidabile per le skill non ancora censite - vedi il commento sopra EFFECT_CLASSES):
    assenti, restano `None` invece di un valore inventato."""

    intent: str
    parameters: dict
    requested_by: str  # stesso formato di ActionReceipt.requested_by: "user"/"agent:<nome>"/"trigger:<nome>"
    risk: str  # core.risk.RiskLevel.value
    effect_class: Optional[str] = None
    preconditions: Optional[str] = None
    expected_effect: Optional[str] = None

    @classmethod
    def for_intent(
        cls, intent: str, parameters: dict, requested_by: str, *,
        effect_class: Optional[str] = None, preconditions: Optional[str] = None,
        expected_effect: Optional[str] = None,
    ) -> "ActionProposal":
        """Costruttore comodo che ricava `risk` da `core.risk.risk_of()` invece di richiederlo al
        chiamante - la stessa fonte gia' usata da `SkillRegistry.risk_of()`/`PolicyEngine.
        register_intent()`, cosi' un ActionProposal non puo' dichiarare un rischio diverso da
        quello che il resto del sistema assegnerebbe allo stesso intent."""
        return cls(
            intent=intent, parameters=dict(parameters or {}), requested_by=requested_by,
            risk=risk_of(intent).value, effect_class=effect_class, preconditions=preconditions,
            expected_effect=expected_effect,
        )


def validate_action_proposal(proposal: ActionProposal) -> None:
    """Contratto minimo, stesso principio di validate_action_receipt (core/action_ledger.py):
    solleva ValueError con il campo incriminato invece di lasciare passare un proposal
    malformato senza che nessuno se ne accorga."""
    if not proposal.intent:
        raise ValueError("ActionProposal.intent e' obbligatorio e non puo' essere vuoto")
    if not proposal.requested_by:
        raise ValueError("ActionProposal.requested_by e' obbligatorio e non puo' essere vuoto")
    if proposal.risk not in {level.value for level in RiskLevel}:
        raise ValueError(f"ActionProposal.risk={proposal.risk!r} non e' un RiskLevel valido")
    if proposal.effect_class is not None and proposal.effect_class not in EFFECT_CLASSES:
        raise ValueError(
            f"ActionProposal.effect_class={proposal.effect_class!r} non valido "
            f"({', '.join(sorted(EFFECT_CLASSES))})"
        )


@dataclass
class ActionContext:
    """Il contesto in cui un'azione viene proposta/eseguita: gli stessi campi (`trace_id`,
    `requested_by`, `private`, `model`) che oggi i quattro chokepoint (JakeCore/TaskAgent/
    PlanExecutor) gia' passano in giro come argomenti posizionali/keyword separati, senza un tipo
    che li raggruppi - vedi ad es. `PlanExecutor._log_step(trace_id, private, model, requested_by,
    ...)`. Formalizzarlo qui non cambia ancora quei chiamanti (F1.1.6/F1.1.7): un tipo pronto per
    quando lo faranno, non un refactor forzato di firme esistenti e gia' testate."""

    trace_id: str
    requested_by: str
    private: bool = False
    model: Optional[str] = None
    ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = time.time()


def validate_action_context(context: ActionContext) -> None:
    if not context.trace_id:
        raise ValueError("ActionContext.trace_id e' obbligatorio e non puo' essere vuoto")
    if not context.requested_by:
        raise ValueError("ActionContext.requested_by e' obbligatorio e non puo' essere vuoto")
    if context.ts <= 0:
        raise ValueError("ActionContext.ts deve essere un timestamp positivo")


@dataclass
class VerificationEvidence:
    """La prova (o l'assenza di prova) che un effetto dichiarato sia avvenuto davvero (F1.3.2/
    F1.3.3). `status` riusa le STESSE tre costanti gia' scritte nel ledger da
    `verification_status_of()` (core/action_ledger.py) - non una quarta rappresentazione
    parallela dello stesso tri-stato. A differenza del semplice booleano `bool | None` che
    `execution_safety.verify_effect()` restituisce oggi, questo tipo puo' portare anche COME e
    QUANDO e' stata fatta la verifica (`method`, `checked_at`) - informazione che oggi si perde
    subito dopo il controllo, mai persistita da nessuna parte."""

    intent: str
    status: str
    method: Optional[str] = None  # es. "filesystem_exists_check" - None se nessun verificatore esiste
    checked_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.checked_at:
            self.checked_at = time.time()

    @classmethod
    def from_verified(cls, intent: str, verified: Optional[bool], method: Optional[str] = None) -> "VerificationEvidence":
        """Converte lo stesso bool|None gia' calcolato da verify_effect(), con la stessa mappatura
        di verification_status_of() (None -> unverified, True -> verified, False -> failed) -
        cosi' un domani in cui execution_safety cambiasse la propria logica bool|None questo tipo
        continua a interpretarla nello stesso modo, senza una seconda funzione di conversione da
        tenere sincronizzata a mano."""
        if verified is None:
            status = VERIFICATION_UNVERIFIED
        else:
            status = VERIFICATION_VERIFIED if verified else VERIFICATION_FAILED
        return cls(intent=intent, status=status, method=method)


def validate_verification_evidence(evidence: VerificationEvidence) -> None:
    if not evidence.intent:
        raise ValueError("VerificationEvidence.intent e' obbligatorio e non puo' essere vuoto")
    if evidence.status not in _VERIFICATION_STATUSES:
        raise ValueError(
            f"VerificationEvidence.status={evidence.status!r} non e' uno stato valido "
            f"({', '.join(_VERIFICATION_STATUSES)})"
        )
    if evidence.checked_at <= 0:
        raise ValueError("VerificationEvidence.checked_at deve essere un timestamp positivo")


@dataclass
class UndoDescriptor:
    """Come annullare UN'azione gia' eseguita con successo (F1.3.5: "generare undo token con
    scadenza e precondizioni"). Distinto da `core.execution_safety.RollbackAction`: quello e' il
    template PER CLASSE DI INTENT (es. "DELETE_PATH e' l'inverso di CREATE_PATH", uguale per ogni
    CREATE_PATH), questo e' l'istanza PER AZIONE GIA' ESEGUITA, con i parametri gia' risolti (es.
    "il path creato era esattamente questo") e uno stato esplicito (`used`/`expires_at`) che il
    template da solo non puo' avere - due esecuzioni dello stesso intent hanno lo stesso
    RollbackAction ma due UndoDescriptor diversi. `expires_at=None` significa "nessuna scadenza
    dichiarata" (non ancora una policy su quanto a lungo un undo debba restare valido - quella
    decisione resta di F1.3.5, non inventata qui)."""

    action_id: str
    compensating_intent: str
    compensating_parameters: dict
    expires_at: Optional[float] = None
    preconditions: Optional[str] = None
    used: bool = False

    def is_expired(self, *, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def is_usable(self, *, now: Optional[float] = None) -> bool:
        return not self.used and not self.is_expired(now=now)


def validate_undo_descriptor(descriptor: UndoDescriptor) -> None:
    if not descriptor.action_id:
        raise ValueError("UndoDescriptor.action_id e' obbligatorio e non puo' essere vuoto")
    if not descriptor.compensating_intent:
        raise ValueError("UndoDescriptor.compensating_intent e' obbligatorio e non puo' essere vuoto")
    if descriptor.expires_at is not None and descriptor.expires_at <= 0:
        raise ValueError("UndoDescriptor.expires_at, se presente, deve essere un timestamp positivo")


@dataclass
class ActionError:
    """Un fallimento descritto in modo strutturato, invece della stringa grezza `result` che oggi
    i quattro chokepoint si passano in giro (es. "error:VERIFICATION_FAILED", "missing_parameters"
    - vedi il commento su _KNOWN_RESULT_CATEGORIES in core/action_ledger.py sui due formati
    diversi in uso). `category` riusa le STESSE costanti `ERROR_CATEGORY_*` gia' introdotte per
    `ActionReceipt.error_category` (F1.1.4) - non una tassonomia parallela. `retryable` riusa
    `execution_safety.RETRYABLE_ERRORS`: NON tiene conto di `is_safe_to_auto_retry()` (F1.3.6,
    che dipende anche dall'INTENT, non solo dal codice di errore) - descrive solo se QUEL codice
    e' della famiglia "transitorio", il chiamante decide ancora se ritentare per quell'intent."""

    category: str
    code: str
    message: Optional[str] = None
    retryable: bool = False

    @classmethod
    def from_result(cls, result: str, message: Optional[str] = None) -> "ActionError":
        code = normalize_result_code(result)
        return cls(
            category=error_category_of(result), code=code, message=message,
            retryable=code in RETRYABLE_ERRORS,
        )


def validate_action_error(error: ActionError) -> None:
    if not error.code:
        raise ValueError("ActionError.code e' obbligatorio e non puo' essere vuoto")
    if error.category not in ERROR_CATEGORIES:
        raise ValueError(f"ActionError.category={error.category!r} non e' una categoria valida")
