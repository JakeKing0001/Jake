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
# e' invece DESTRUCTIVE) - i due assi non si derivano l'uno dall'altro in modo affidabile, ne'
# l'uno dal nome dell'intent: serve leggere il comportamento REALE di ogni skill (vedi
# INTENT_EFFECT_CLASS sotto).
EFFECT_CLASS_READ = "read"
EFFECT_CLASS_CREATE = "create"
EFFECT_CLASS_MODIFY = "modify"
EFFECT_CLASS_DELETE = "delete"
EFFECT_CLASS_EXTERNAL = "external"
EFFECT_CLASSES = frozenset({
    EFFECT_CLASS_READ, EFFECT_CLASS_CREATE, EFFECT_CLASS_MODIFY, EFFECT_CLASS_DELETE,
    EFFECT_CLASS_EXTERNAL,
})

# F1.1.2 (censimento completo, 15/09/2026): 208 dei 209 intent di core/risk.py::SKILL_RISK,
# classificati leggendo il comportamento REALE di ogni execute() (non ipotizzato dal nome
# dell'intent - lo stesso errore che il commento sopra EFFECT_CLASSES avvertiva di evitare).
# Manca deliberatamente `RESUME_INTERRUPTED_TASK`: riprende un TaskAgent da un checkpoint
# salvato, e l'agente ripreso decide da solo il prossimo passo (che puo' essere qualunque cosa) -
# core/risk.py lo classifica gia' EXTERNAL_ACTION con la stessa identica motivazione esplicita
# ("i passi che l'agente ripreso decide di fare non sono ispezionati qui"); forzare una scelta
# qui significherebbe indovinare, non censire. RUN_COMMAND/RUN_PYTHON_SCRIPT sono "external" non
# perche' ovvio dal nome, ma perche' la categoria "external" e' definita apposta per un effetto
# fuori dal controllo/conoscenza diretta di Jake - un comando/script arbitrario e' l'esempio piu'
# diretto possibile di quella definizione, non un ripiego per "non so cosa fa". Un intent NON
# elencato qui (un plugin di terze parti, una skill appena forgiata mai censita) non ha un
# default "piu' prudente" plausibile tra le cinque classi - a differenza di risk_of(), che
# ricade su ADMIN come scelta di sicurezza, qui non esiste un ordine di gravita' da cui dedurre
# un ripiego: `effect_class_of()` restituisce None piuttosto che inventare una classificazione.
INTENT_EFFECT_CLASS: dict[str, str] = {
    "ADD_NOTE": EFFECT_CLASS_CREATE,
    "ADD_TODO": EFFECT_CLASS_CREATE,
    "ASK_QUESTION": EFFECT_CLASS_READ,
    "BUILD_SEMANTIC_INDEX": EFFECT_CLASS_CREATE,
    "CALCULATE": EFFECT_CLASS_READ,
    "CALCULATE_AGE": EFFECT_CLASS_READ,
    "CALCULATE_BMI": EFFECT_CLASS_READ,
    "CALCULATE_DISCOUNT": EFFECT_CLASS_READ,
    "CALCULATE_PERCENTAGE": EFFECT_CLASS_READ,
    "CALCULATE_TIP": EFFECT_CLASS_READ,
    "CANCEL_TIMER": EFFECT_CLASS_DELETE,
    "CHECK_FILE_HASH": EFFECT_CLASS_READ,
    "CHECK_PASSWORD_STRENGTH": EFFECT_CLASS_READ,
    "CHECK_PORT_IN_USE": EFFECT_CLASS_READ,
    "CHECK_WEBSITE_STATUS": EFFECT_CLASS_READ,
    "CHITCHAT": EFFECT_CLASS_READ,
    "CHOOSE_RANDOM": EFFECT_CLASS_READ,
    "CLEAR_NOTES": EFFECT_CLASS_DELETE,
    "CLEAR_TEMP_FILES": EFFECT_CLASS_DELETE,
    "CLICK_ELEMENT": EFFECT_CLASS_MODIFY,
    "CLICK_MOUSE": EFFECT_CLASS_MODIFY,
    "CLICK_TEXT": EFFECT_CLASS_MODIFY,
    "CLIPBOARD_READ": EFFECT_CLASS_READ,
    "CLIPBOARD_WRITE": EFFECT_CLASS_MODIFY,
    "CLOSE_APP": EFFECT_CLASS_DELETE,  # termina il processo se non ha finestre visibili, non solo le chiude (diverso da CLOSE_WINDOW)
    "CLOSE_WINDOW": EFFECT_CLASS_MODIFY,  # solo WM_CLOSE: il processo puo' sopravvivere (diverso da CLOSE_APP)
    "COMPLETE_TODO": EFFECT_CLASS_MODIFY,
    "COMPRESS_PATH": EFFECT_CLASS_CREATE,
    "CONTROL_SMART_DEVICE": EFFECT_CLASS_EXTERNAL,
    "CONVERT_CASE": EFFECT_CLASS_READ,
    "CONVERT_CURRENCY": EFFECT_CLASS_READ,
    "CONVERT_MORSE_CODE": EFFECT_CLASS_READ,
    "CONVERT_NUMBER_TO_WORDS": EFFECT_CLASS_READ,
    "CONVERT_ROMAN_NUMERAL": EFFECT_CLASS_READ,
    "CONVERT_TIMEZONE": EFFECT_CLASS_READ,
    "CONVERT_UNITS": EFFECT_CLASS_READ,
    "CORRECT_LAST": EFFECT_CLASS_MODIFY,
    "COUNT_LINES_OF_CODE": EFFECT_CLASS_READ,
    "COUNT_WORDS": EFFECT_CLASS_READ,
    "COUNT_WORDS_IN_FILE": EFFECT_CLASS_READ,
    "CREATE_PATH": EFFECT_CLASS_CREATE,
    "CREATE_SKILL": EFFECT_CLASS_CREATE,
    "DAYS_UNTIL": EFFECT_CLASS_READ,
    "DELETE_CREATED_SKILL": EFFECT_CLASS_DELETE,
    "DELETE_PATH": EFFECT_CLASS_DELETE,
    "DELETE_REMINDER": EFFECT_CLASS_DELETE,
    "DELETE_TODO": EFFECT_CLASS_DELETE,
    "DELETE_TRIGGER": EFFECT_CLASS_DELETE,
    "DESCRIBE_SCREEN": EFFECT_CLASS_READ,
    "DETECT_LANGUAGE": EFFECT_CLASS_READ,
    "DUPLICATE_FILE": EFFECT_CLASS_CREATE,
    "EMPTY_CLIPBOARD": EFFECT_CLASS_DELETE,  # rimuove il contenuto esistente senza sostituirlo (diverso da CLIPBOARD_WRITE)
    "EMPTY_RECYCLE_BIN": EFFECT_CLASS_DELETE,
    "EXPORT_NOTES": EFFECT_CLASS_CREATE,
    "EXTRACT_ARCHIVE": EFFECT_CLASS_CREATE,
    "EXTRACT_URLS_FROM_TEXT": EFFECT_CLASS_READ,
    "FIBONACCI": EFFECT_CLASS_READ,
    "FIND_DUPLICATE_FILES": EFFECT_CLASS_READ,
    "FIND_FILE": EFFECT_CLASS_READ,
    "FIND_LARGE_FILES": EFFECT_CLASS_READ,
    "FLIP_COIN": EFFECT_CLASS_READ,
    "FLUSH_DNS": EFFECT_CLASS_DELETE,  # rimuove voci di cache DNS esistenti
    "FOCUS_WINDOW": EFFECT_CLASS_MODIFY,
    "FORGET": EFFECT_CLASS_DELETE,
    "FORGET_LEARNED": EFFECT_CLASS_DELETE,
    "FORMAT_JSON": EFFECT_CLASS_READ,
    "GCD_LCM": EFFECT_CLASS_READ,
    "GENERATE_PASSWORD": EFFECT_CLASS_READ,
    "GENERATE_UUID": EFFECT_CLASS_READ,
    "GET_ACTIVE_WINDOW": EFFECT_CLASS_READ,
    "GET_BATTERY_STATUS": EFFECT_CLASS_READ,
    "GET_BROWSER_HISTORY": EFFECT_CLASS_READ,
    "GET_CPU_USAGE": EFFECT_CLASS_READ,
    "GET_DATE": EFFECT_CLASS_READ,
    "GET_DAY_OF_WEEK": EFFECT_CLASS_READ,
    "GET_DISK_USAGE": EFFECT_CLASS_READ,
    "GET_DNS_SERVERS": EFFECT_CLASS_READ,
    "GET_ENVIRONMENT_VARIABLE": EFFECT_CLASS_READ,
    "GET_FILE_INFO": EFFECT_CLASS_READ,
    "GET_FOLDER_SIZE": EFFECT_CLASS_READ,
    "GET_GPU_INFO": EFFECT_CLASS_READ,
    "GET_LOCAL_IP": EFFECT_CLASS_READ,
    "GET_MAC_ADDRESS": EFFECT_CLASS_READ,
    "GET_MEMORY_USAGE": EFFECT_CLASS_READ,
    "GET_MOON_PHASE": EFFECT_CLASS_READ,
    "GET_NEWS": EFFECT_CLASS_READ,
    "GET_NEXT_HOLIDAY": EFFECT_CLASS_READ,
    "GET_NOTIFICATION_MODE": EFFECT_CLASS_READ,
    "GET_PUBLIC_IP": EFFECT_CLASS_READ,
    "GET_SCREEN_RESOLUTION": EFFECT_CLASS_READ,
    "GET_SUNRISE_SUNSET": EFFECT_CLASS_READ,
    "GET_SYSTEM_INFO": EFFECT_CLASS_READ,
    "GET_TIME": EFFECT_CLASS_READ,
    "GET_UPTIME": EFFECT_CLASS_READ,
    "GET_VOLUME_LEVEL": EFFECT_CLASS_READ,
    "GET_WEATHER": EFFECT_CLASS_READ,
    "GET_WEEK_NUMBER": EFFECT_CLASS_READ,
    "GET_WIFI_STATUS": EFFECT_CLASS_READ,
    "GIT_BRANCH": EFFECT_CLASS_READ,
    "GIT_DIFF": EFFECT_CLASS_READ,
    "GIT_LOG": EFFECT_CLASS_READ,
    "GIT_PULL": EFFECT_CLASS_EXTERNAL,  # porta dentro contenuto da un remoto non controllato da Jake
    "GIT_STATUS": EFFECT_CLASS_READ,
    "HELP": EFFECT_CLASS_READ,
    "HYBRID_SEARCH_FILES": EFFECT_CLASS_READ,
    "IS_PRIME": EFFECT_CLASS_READ,
    "KILL_PROCESS_BY_PORT": EFFECT_CLASS_DELETE,
    "KILL_SWITCH": EFFECT_CLASS_MODIFY,
    "LEARN_COMMAND": EFFECT_CLASS_CREATE,
    "LINK_MEMORY": EFFECT_CLASS_CREATE,
    "LIST_CONTACTS": EFFECT_CLASS_READ,
    "LIST_CREATED_SKILLS": EFFECT_CLASS_READ,
    "LIST_DRIVES": EFFECT_CLASS_READ,
    "LIST_INSTALLED_APPS": EFFECT_CLASS_READ,
    "LIST_LEARNED": EFFECT_CLASS_READ,
    "LIST_MODELS": EFFECT_CLASS_READ,
    "LIST_NOTES": EFFECT_CLASS_READ,
    "LIST_OPEN_WINDOWS": EFFECT_CLASS_READ,
    "LIST_PROCESSES": EFFECT_CLASS_READ,
    "LIST_RECENT_FILES": EFFECT_CLASS_READ,
    "LIST_REMINDERS": EFFECT_CLASS_READ,
    "LIST_SMART_DEVICES": EFFECT_CLASS_READ,
    "LIST_STARTUP_APPS": EFFECT_CLASS_READ,
    "LIST_TIMERS": EFFECT_CLASS_READ,
    "LIST_TODOS": EFFECT_CLASS_READ,
    "LIST_TRIGGERS": EFFECT_CLASS_READ,
    "LIST_WIFI_NETWORKS": EFFECT_CLASS_READ,
    "MAGIC_8_BALL": EFFECT_CLASS_READ,
    "MAXIMIZE_WINDOW": EFFECT_CLASS_MODIFY,
    "MEDIA_CONTROL": EFFECT_CLASS_MODIFY,
    "MINIMIZE_ALL_WINDOWS": EFFECT_CLASS_MODIFY,
    "MINIMIZE_WINDOW": EFFECT_CLASS_MODIFY,
    "MOVE_MOUSE": EFFECT_CLASS_MODIFY,
    "MOVE_PATH": EFFECT_CLASS_MODIFY,
    "OPEN_APP": EFFECT_CLASS_EXTERNAL,
    "OPEN_INCOGNITO_WINDOW": EFFECT_CLASS_EXTERNAL,
    "OPEN_IN_EDITOR": EFFECT_CLASS_EXTERNAL,
    "OPEN_PATH": EFFECT_CLASS_EXTERNAL,
    "OPEN_SEARCH_RESULT": EFFECT_CLASS_EXTERNAL,
    "OPEN_URL": EFFECT_CLASS_EXTERNAL,
    "PAUSE_LISTENING": EFFECT_CLASS_MODIFY,
    "PING_HOST": EFFECT_CLASS_READ,
    "PLAY_MEDIA": EFFECT_CLASS_EXTERNAL,
    "PRESS_KEY": EFFECT_CLASS_MODIFY,
    "PRINT_FILE": EFFECT_CLASS_EXTERNAL,
    "PROOFREAD_TEXT": EFFECT_CLASS_READ,
    "PURGE_OLD_HISTORY": EFFECT_CLASS_DELETE,
    "RANDOM_FACT": EFFECT_CLASS_READ,
    "RANDOM_NUMBER": EFFECT_CLASS_READ,
    "RANDOM_QUOTE": EFFECT_CLASS_READ,
    "READ_FILE_TEXT": EFFECT_CLASS_READ,
    "READ_SCREEN": EFFECT_CLASS_READ,
    "READ_SELECTION": EFFECT_CLASS_READ,
    "READ_WEB_PAGE": EFFECT_CLASS_READ,
    "RECALL": EFFECT_CLASS_READ,
    "REMEMBER": EFFECT_CLASS_CREATE,
    "RENAME_PATH": EFFECT_CLASS_MODIFY,
    "REPEAT_LAST": EFFECT_CLASS_READ,
    "RESEARCH": EFFECT_CLASS_READ,
    "RESET_KILL_SWITCH": EFFECT_CLASS_MODIFY,
    "RESIZE_WINDOW": EFFECT_CLASS_MODIFY,
    "RESTART_EXPLORER": EFFECT_CLASS_MODIFY,
    "RESTORE_WINDOW": EFFECT_CLASS_MODIFY,
    "ROCK_PAPER_SCISSORS": EFFECT_CLASS_READ,
    "ROLL_DICE": EFFECT_CLASS_READ,
    "RUN_COMMAND": EFFECT_CLASS_EXTERNAL,
    "RUN_COMPUTER_PROCEDURE": EFFECT_CLASS_EXTERNAL,  # esegue passi salvati in precedenza, non ispezionati qui
    "RUN_PYTHON_SCRIPT": EFFECT_CLASS_EXTERNAL,
    "RUN_WORKFLOW": EFFECT_CLASS_EXTERNAL,  # esegue passi salvati in precedenza, non ispezionati qui
    "SAVE_CONTACT": EFFECT_CLASS_CREATE,
    "SAVE_WORKFLOW": EFFECT_CLASS_CREATE,
    "SCROLL": EFFECT_CLASS_MODIFY,
    "SEARCH_FILES": EFFECT_CLASS_READ,
    "SEARCH_IN_BROWSER": EFFECT_CLASS_EXTERNAL,
    "SEARCH_NOTES": EFFECT_CLASS_READ,
    "SEMANTIC_SEARCH_FILES": EFFECT_CLASS_READ,
    "SEND_EMAIL": EFFECT_CLASS_EXTERNAL,  # apre mailto:, serve un click umano - non invia da solo
    "SEND_WHATSAPP": EFFECT_CLASS_EXTERNAL,  # apre wa.me con testo precompilato - non trasmette da solo
    "SET_BRIGHTNESS": EFFECT_CLASS_MODIFY,
    "SET_DAILY_REMINDER": EFFECT_CLASS_CREATE,
    "SET_MODEL": EFFECT_CLASS_MODIFY,
    "SET_NOTIFICATION_MODE": EFFECT_CLASS_MODIFY,
    "SET_POWER_PLAN": EFFECT_CLASS_MODIFY,
    "SET_PRIVATE_MODE": EFFECT_CLASS_MODIFY,
    "SET_REMINDER": EFFECT_CLASS_CREATE,
    "SET_TIMER": EFFECT_CLASS_CREATE,
    "SET_TRIGGER": EFFECT_CLASS_CREATE,
    "SET_VOLUME": EFFECT_CLASS_MODIFY,
    "SET_VOLUME_LEVEL": EFFECT_CLASS_MODIFY,
    "SET_WINDOW_ALWAYS_ON_TOP": EFFECT_CLASS_MODIFY,
    "SNAP_WINDOW_LEFT": EFFECT_CLASS_MODIFY,
    "SNAP_WINDOW_RIGHT": EFFECT_CLASS_MODIFY,
    "SNOOZE_REMINDER": EFFECT_CLASS_MODIFY,
    "START_DICTATION": EFFECT_CLASS_MODIFY,
    "START_POMODORO": EFFECT_CLASS_CREATE,  # internamente e' reminder_manager.add(), identico a SET_REMINDER
    "STOP_DICTATION": EFFECT_CLASS_MODIFY,
    "STOP_POMODORO": EFFECT_CLASS_DELETE,  # internamente e' reminder_manager.delete_matching(), identico a DELETE_REMINDER
    "STOP_TALKING": EFFECT_CLASS_MODIFY,
    "SUMMARIZE_CLIPBOARD": EFFECT_CLASS_READ,  # riassume via Ollama ma NON riscrive il risultato negli appunti
    "SUMMARIZE_TEXT": EFFECT_CLASS_READ,
    "SWITCH_NEXT_WINDOW": EFFECT_CLASS_MODIFY,
    "SYSTEM_POWER": EFFECT_CLASS_EXTERNAL,
    "TAKE_SCREENSHOT": EFFECT_CLASS_CREATE,
    "TELL_JOKE": EFFECT_CLASS_READ,
    "TOGGLE_DARK_MODE": EFFECT_CLASS_MODIFY,
    "TRACE_ROUTE": EFFECT_CLASS_READ,
    "TRANSLATE_CLIPBOARD": EFFECT_CLASS_READ,  # traduce via Ollama ma NON riscrive il risultato negli appunti
    "TRANSLATE_TEXT": EFFECT_CLASS_READ,
    "TYPE_TEXT": EFFECT_CLASS_MODIFY,
    "UNDO_LAST_ACTION": EFFECT_CLASS_READ,  # propone solo una busta CONFIRMATION_REQUIRED, non muta nulla direttamente
    "WEB_SEARCH": EFFECT_CLASS_READ,
}


def effect_class_of(intent: str) -> Optional[str]:
    """Classificazione EFFECT_CLASS_* per un intent gia' censito (INTENT_EFFECT_CLASS sopra),
    None se non censito - a differenza di risk_of() (core/risk.py, ricade su ADMIN come scelta
    di sicurezza per default), qui non esiste un default "piu' prudente" plausibile tra le
    cinque classi: sono una tassonomia del TIPO di effetto, non un ordinamento di gravita'.
    Inventare una classificazione sarebbe peggio che dichiarare onestamente "non censito"."""
    return INTENT_EFFECT_CLASS.get(intent)


@dataclass
class ActionProposal:
    """Cio' che un agente/planner/skill PROPONE di fare, prima che il PolicyEngine decida (F1.1.2:
    "intent, parametri tipizzati, rischio, scope, precondizioni, effetto atteso" - vedi la tabella
    dei contratti condivisi in ROADMAP_EXECUTION.md, sezione 4.2). Oggi questa informazione esiste
    solo in modo implicito e sparso (l'`intent`/`parameters` passati a `PolicyEngine.
    decide_interactive`/`decide_automated`, il rischio ricavato al volo da `risk_of()`): niente la
    raggruppa in un unico oggetto ispezionabile PRIMA dell'esecuzione. `effect_class` e'
    facoltativo ma ora derivato AUTOMATICAMENTE da `effect_class_of()` (censimento completo, vedi
    sopra) quando il chiamante non lo passa esplicitamente - un valore passato esplicitamente
    vince sempre (`for_intent()` non sovrascrive mai una scelta deliberata del chiamante).
    `preconditions`/`expected_effect` restano dichiarati dal chiamante, nessun censimento
    equivalente esiste ancora per loro: assenti, restano `None` invece di un valore inventato."""

    intent: str
    parameters: dict
    requested_by: str  # stesso formato di ActionReceipt.requested_by: "user"/"agent:<nome>"/"trigger:<nome>"
    risk: str  # core.risk.RiskLevel.value
    effect_class: Optional[str] = None
    preconditions: Optional[str] = None
    expected_effect: Optional[str] = None

    @classmethod
    def for_intent(
        cls, intent: str, parameters: Optional[dict], requested_by: str, *,
        effect_class: Optional[str] = None, preconditions: Optional[str] = None,
        expected_effect: Optional[str] = None,
    ) -> "ActionProposal":
        """Costruttore comodo che ricava `risk` da `core.risk.risk_of()` invece di richiederlo al
        chiamante - la stessa fonte gia' usata da `SkillRegistry.risk_of()`/`PolicyEngine.
        register_intent()`, cosi' un ActionProposal non puo' dichiarare un rischio diverso da
        quello che il resto del sistema assegnerebbe allo stesso intent. Stesso principio ora
        anche per `effect_class`: se il chiamante non lo passa (None, il default), viene ricavato
        da `effect_class_of()` invece di restare sempre None come prima del censimento - se il
        chiamante lo passa esplicitamente, quella scelta vince sempre (mai sovrascritta)."""
        return cls(
            intent=intent, parameters=dict(parameters or {}), requested_by=requested_by,
            risk=risk_of(intent).value,
            effect_class=effect_class if effect_class is not None else effect_class_of(intent),
            preconditions=preconditions,
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
