"""Action ledger append-only (F1, Trustworthy Agent Core 3.0): "chi ha chiesto cosa, quale
agente ha deciso, quale skill ha agito, con quale autorizzazione e quale risultato" - vedi la
fase F1 in ROADMAP.md.

Diverso da core/logger.log_action (F0): quello ruota (max 2 MB x 4 file, pensato per il debug
quotidiano - vecchie righe vengono scartate di proposito per non riempire il disco). Questo non
ruota mai: un registro di controllo non deve perdere silenziosamente le voci vecchie. La crescita
illimitata resta un limite noto e dichiarato, non risolto qui - servirebbe una policy di
retention/archiviazione esplicita (F1 la elenca insieme a Privacy Engine, fase 5.6), non una
rotazione silenziosa che la aggirerebbe di nascosto.

action_id identifica UNA azione (un passo dell'agente, un comando singolo); trace_id (core/
logger.py) correla invece TUTTI i passi di una stessa richiesta/compito. authorization e'
derivata da segnali gia' presenti nel sistema (risk.py, i parametri "confirmed"/"authenticated"
gia' usati da JakeCore._resolve_and_execute per riconoscere una richiesta gia' confermata), non
un campo inventato: "none" quando l'azione non ha mai avuto bisogno di autorizzazione,
"confirmed"/"passphrase" quando l'ha ricevuta, "pending"/"blocked" quando e' in attesa o negata."""
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from core.logger import new_trace_id

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_ledger.jsonl"

# Stesso generatore di core.logger.new_trace_id (uuid4 troncato): action_id e trace_id sono
# concettualmente la stessa cosa (un id breve per correlare righe di log), usati per due scopi
# diversi - un alias invece di duplicare la stessa funzione con un nome diverso.
new_action_id = new_trace_id

AUTHORIZATION_NONE = "none"
AUTHORIZATION_CONFIRMED = "confirmed"
AUTHORIZATION_PASSPHRASE = "passphrase"
AUTHORIZATION_WINDOWS_HELLO = "windows_hello"
AUTHORIZATION_PENDING = "pending"
AUTHORIZATION_BLOCKED = "blocked"
AUTHORIZATION_DENIED = "denied"


def authorization_of(result: str, parameters: dict | None) -> str:
    """Deriva lo stato di autorizzazione dagli stessi segnali gia' usati altrove (core/risk.py,
    JakeCore._resolve_and_execute), invece di chiedere a chi registra la ricevuta di dichiararlo
    a mano - due fonti diverse per lo stesso fatto potrebbero disallinearsi in silenzio.

    authenticated_via (F1) distingue Windows Hello dalla passphrase quando entrambi sono attivi
    (core/auth_gate.py): "passphrase" resta il default per compatibilita' con le ricevute scritte
    prima che questo campo esistesse (authenticated=True senza authenticated_via).

    "denied_auth"/"denied_confirmation" (F1) distinguono un diniego vero - passphrase sbagliata,
    o l'utente che risponde "no" a una richiesta di conferma - da una richiesta ancora in attesa
    (AUTHORIZATION_PENDING): a differenza di quella, qui l'azione non partira' piu' per questo
    turno, ed e' comunque un evento di sicurezza degno di una ricevuta (vedi ROADMAP.md, F1)."""
    parameters = parameters or {}
    if result in ("blocked_by_policy", "policy_blocked"):
        return AUTHORIZATION_BLOCKED
    if result in ("denied_auth", "denied_confirmation"):
        return AUTHORIZATION_DENIED
    if result in ("confirmation_required", "auth_required"):
        return AUTHORIZATION_PENDING
    if parameters.get("authenticated"):
        if parameters.get("authenticated_via") == "windows_hello":
            return AUTHORIZATION_WINDOWS_HELLO
        return AUTHORIZATION_PASSPHRASE
    if parameters.get("confirmed"):
        return AUTHORIZATION_CONFIRMED
    return AUTHORIZATION_NONE


def idempotency_key_of(intent: str, parameters: dict | None) -> str:
    """Chiave stabile per la STESSA azione logica (stesso intent, stessi parametri): permette di
    accorgersi - in audit, o in futuro per un'enforcement vera - se un'azione e' stata eseguita
    piu' volte quando non doveva (un retry che non andava ripetuto, un trigger partito due volte
    per una race, un agente che ripete un passo per un bug del modello). Non impedisce ancora
    l'esecuzione doppia: farlo richiederebbe decidere cosa succede quando una chiave combacia
    (rifiutare? restituire il risultato precedente? con quale scadenza?), una decisione di
    policy che merita una revisione dedicata, non un effetto collaterale di questo campo - vedi
    la fase F1 in ROADMAP.md. Per ora e' solo tracciata nel ledger, pronta per quando
    quell'enforcement arrivera'; TaskAgent.run() ha gia' una propria protezione piu' debole e
    locale (il set `seen` che ferma un passo identico ripetuto nello STESSO compito, non tra
    compiti/sessioni diverse - vedi core/agent.py)."""
    canonical = json.dumps({"intent": intent, "parameters": parameters or {}}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# F1.1.5: unica versione supportata finche' non esiste una vera migrazione. read_all()/by_*
# restano permissivi sui record vecchi gia' su disco (letti come dict, non ri-costruiti come
# ActionReceipt), quindi non serve retrocompatibilita' qui: questo campo riguarda solo le
# ricevute create da adesso in poi, non quelle gia' scritte prima che esistesse.
ACTION_RECEIPT_SCHEMA_VERSION = 1

# F1.3.3: tre stati espliciti, mai un quarto "non detto" - prima di questa correzione
# ActionReceipt.verified era Optional[bool] e to_json() ometteva il campo quando None (vedi
# sotto), cosi' una ricevuta "mai verificata" finiva nel ledger IDENTICA a una scritta da uno
# schema piu' vecchio senza questo campo: nessun modo di distinguere "non c'e' un verificatore
# per questo intent" da "questa versione del ledger non registrava ancora la verifica". Ora il
# campo e' sempre una di queste tre stringhe, mai assente.
VERIFICATION_VERIFIED = "verified"
VERIFICATION_UNVERIFIED = "unverified"
VERIFICATION_FAILED = "verification_failed"
_VERIFICATION_STATUSES = (VERIFICATION_VERIFIED, VERIFICATION_UNVERIFIED, VERIFICATION_FAILED)


# F1.1.4 ("definire tassonomia errori"): le nove categorie elencate in ROADMAP_EXECUTION.md
# per un ActionError, piu' due che il ledger deve comunque poter esprimere perche' NON ogni
# ricevuta e' un errore ("success" per un'azione riuscita, "pending" per una che aspetta ancora
# una risposta dell'utente) - senza le due extra, ogni ricevuta non fallita finirebbe nel
# fallback "uncategorized" insieme ai veri errori non ancora mappati, rendendo il campo inutile
# per distinguere "e' andata bene" da "e' fallita in un modo che non conosciamo ancora".
ERROR_CATEGORY_SUCCESS = "success"
ERROR_CATEGORY_PENDING = "pending"
ERROR_CATEGORY_DENIED = "denied"
ERROR_CATEGORY_INVALID_INPUT = "invalid_input"
ERROR_CATEGORY_UNAVAILABLE = "unavailable"
ERROR_CATEGORY_TRANSIENT = "transient"
ERROR_CATEGORY_TIMEOUT = "timeout"
ERROR_CATEGORY_PARTIAL_EFFECT = "partial_effect"
ERROR_CATEGORY_VERIFICATION_FAILED = "verification_failed"
ERROR_CATEGORY_CONFLICT = "conflict"
ERROR_CATEGORY_USER_CANCELLED = "user_cancelled"
# Nessuna delle ~200 skill produce ancora un codice di errore pensato per questa tassonomia
# (F1.1.6/F1.1.7, migrazione skill-per-skill, restano lavoro successivo): un codice bespoke di
# una singola skill (es. "PATH_NOT_FOUND", "PROTECTED_PATH") finisce onestamente qui, invece di
# essere forzato in una delle categorie sopra solo per evitare questo valore.
ERROR_CATEGORY_UNCATEGORIZED = "uncategorized"

# Pubblico (non _ERROR_CATEGORIES): core/action_contracts.py::validate_action_error (F1.1.2) lo
# riusa per non duplicare l'elenco delle categorie valide in un secondo posto.
ERROR_CATEGORIES = frozenset({
    ERROR_CATEGORY_SUCCESS, ERROR_CATEGORY_PENDING, ERROR_CATEGORY_DENIED,
    ERROR_CATEGORY_INVALID_INPUT, ERROR_CATEGORY_UNAVAILABLE, ERROR_CATEGORY_TRANSIENT,
    ERROR_CATEGORY_TIMEOUT, ERROR_CATEGORY_PARTIAL_EFFECT, ERROR_CATEGORY_VERIFICATION_FAILED,
    ERROR_CATEGORY_CONFLICT, ERROR_CATEGORY_USER_CANCELLED, ERROR_CATEGORY_UNCATEGORIZED,
})

# Da codice/esito grezzo (maiuscolo, MINUSCOLO, con o senza il prefisso "error:") alla categoria:
# i quattro chokepoint che scrivono una ricevuta (JakeCore._log_action_outcome/_log_denied_action,
# TaskAgent._log_step, PlanExecutor._log_step) NON condividono lo stesso formato per `result`
# (letto direttamente dal codice, non ipotizzato: PlanExecutor usa "error:VERIFICATION_FAILED"
# preservando il caso originale della skill, TaskAgent a volte usa "missing_parameters" gia' in
# minuscolo e senza prefisso, JakeCore usa "blocked_by_policy"/"skill_not_found" - unificare quel
# formato e' un cambiamento piu' ampio, rimandato) - error_category_of() normalizza entrambi
# invece di richiedere ai chiamanti di farlo.
_KNOWN_RESULT_CATEGORIES: dict[str, str] = {
    "SUCCESS": ERROR_CATEGORY_SUCCESS,
    "CONFIRMATION_REQUIRED": ERROR_CATEGORY_PENDING,
    "AUTH_REQUIRED": ERROR_CATEGORY_PENDING,
    "BLOCKED_BY_POLICY": ERROR_CATEGORY_DENIED,
    "POLICY_BLOCKED": ERROR_CATEGORY_DENIED,
    "DENIED_AUTH": ERROR_CATEGORY_DENIED,
    "DENIED_CONFIRMATION": ERROR_CATEGORY_DENIED,
    "MISSING_PARAMETERS": ERROR_CATEGORY_INVALID_INPUT,
    "SKILL_NOT_FOUND": ERROR_CATEGORY_INVALID_INPUT,
    "UNKNOWN_INTENT": ERROR_CATEGORY_INVALID_INPUT,
    "INVALID_PLAN_RESPONSE": ERROR_CATEGORY_INVALID_INPUT,
    "NO_RESULT": ERROR_CATEGORY_INVALID_INPUT,
    "OLLAMA_UNAVAILABLE": ERROR_CATEGORY_UNAVAILABLE,
    "PLANNER_ERROR": ERROR_CATEGORY_UNAVAILABLE,
    # Le stesse due costanti gia' condivise da core/execution_safety.py::RETRYABLE_ERRORS: un
    # errore che TaskAgent/PlanExecutor ritentano gia' automaticamente prima di arrendersi.
    "OPERATION_FAILED": ERROR_CATEGORY_TRANSIENT,
    "NETWORK_UNAVAILABLE": ERROR_CATEGORY_TRANSIENT,
    "TIMEOUT": ERROR_CATEGORY_TIMEOUT,
    "VERIFICATION_FAILED": ERROR_CATEGORY_VERIFICATION_FAILED,
    # Il kill switch e' un comando esplicito dell'utente ("ferma tutto"), non un errore del
    # sistema: un passo interrotto da li' e' un annullamento voluto, non un fallimento da capire.
    "KILLED": ERROR_CATEGORY_USER_CANCELLED,
}


def normalize_result_code(result: str) -> str:
    """Spoglia un `result` gia' calcolato dai quattro chokepoint del prefisso "error:" (se
    presente) e lo porta in MAIUSCOLO, per confrontarlo con un codice canonico indipendentemente
    da quale dei due formati in uso oggi lo abbia scritto (vedi il commento su
    _KNOWN_RESULT_CATEGORIES). Estratta da error_category_of() perche' anche
    core/action_contracts.py::ActionError.from_result() (F1.1.2) ha bisogno dello stesso codice
    normalizzato, non solo della categoria - una sola funzione invece di due copie della stessa
    normalizzazione."""
    normalized = (result or "").strip()
    if normalized.lower().startswith("error:"):
        normalized = normalized[len("error:"):]
    return normalized.upper()


def error_category_of(result: str) -> str:
    """F1.1.4: categorizza un `result` gia' calcolato dai quattro chokepoint in una delle
    categorie della tassonomia sopra, tollerando i due formati diversi in uso oggi (vedi il
    commento su _KNOWN_RESULT_CATEGORIES). Un `result` con prefisso "error:" (es.
    "error:MISSING_PARAMETERS") viene spogliato del prefisso prima del confronto. Un codice non
    ancora mappato (quasi sempre un errore specifico di UNA skill, non ancora migrata sulla
    tassonomia condivisa - F1.1.6/F1.1.7) ricade su ERROR_CATEGORY_UNCATEGORIZED invece di
    sollevare un errore o di essere forzato in una categoria sbagliata solo per evitarlo."""
    return _KNOWN_RESULT_CATEGORIES.get(normalize_result_code(result), ERROR_CATEGORY_UNCATEGORIZED)


def verification_status_of(verified: Optional[bool]) -> str:
    """Converte il tri-stato bool|None gia' calcolato da TaskAgent/PlanExecutor (vedi
    core/execution_safety.py::verify_effect) nella stringa esplicita da salvare nel ledger:
    None (nessun verificatore indipendente per questo intent) -> "unverified", True -> "verified",
    False (il verificatore ha girato e ha trovato l'effetto mancante) -> "verification_failed".
    I chiamanti non cambiano la propria logica bool|None, gia' corretta: solo il valore scritto
    nel ledger diventa esplicito invece di sparire quando None."""
    if verified is None:
        return VERIFICATION_UNVERIFIED
    return VERIFICATION_VERIFIED if verified else VERIFICATION_FAILED


@dataclass
class ActionReceipt:
    action_id: str
    trace_id: str
    ts: float
    intent: str
    requested_by: str  # "user" | "agent:<general|coding|research>" | "trigger:<nome>"
    risk_decision: str
    authorization: str
    result: str
    # F1.1.3: obbligatoria, non Optional - i 4 punti che costruiscono una ricevuta (JakeCore.
    # _log_action_outcome/_log_denied_action, TaskAgent._log_step, PlanExecutor._log_step) la
    # calcolano gia' sempre con idempotency_key_of(); renderla facoltativa qui nasconderebbe in
    # silenzio un futuro punto che se ne dimenticasse (vedi validate_action_receipt).
    idempotency_key: str
    # F1.3.3: default "unverified", mai None - vedi verification_status_of() e il commento sopra
    # le tre costanti. JakeCore._log_action_outcome/_log_denied_action non passano mai questo
    # campo (il percorso a comando singolo non verifica ancora l'effetto): il default lo rende
    # comunque esplicito nel ledger invece di ometterlo.
    verified: str = VERIFICATION_UNVERIFIED
    # F1.1.4: default ERROR_CATEGORY_UNCATEGORIZED invece di ricalcolarlo da `result` qui dentro -
    # stesso principio di `authorization`/`verified`, gia' calcolati dal CHIAMANTE (authorization_of/
    # verification_status_of) invece che dalla dataclass stessa, cosi' una ricevuta costruita da un
    # test o da codice futuro senza passare per error_category_of() resta esplicita (uncategorized)
    # invece di sembrare "success" per caso.
    error_category: str = ERROR_CATEGORY_UNCATEGORIZED
    # F1.2.6 ("salvare la motivazione della decisione nel ledger senza salvare segreti"):
    # None quando nessun policy_engine era disponibile nel punto che ha scritto la ricevuta
    # (oggi solo PlanExecutor._log_step lo passa - vedi core/policy_engine.py::POLICY_REASONS,
    # un vocabolario CHIUSO di quattro costanti, mai testo libero costruito da parametri: e'
    # questo che rende impossibile, non solo evitato per convenzione, che un segreto (un token,
    # un percorso privato) finisca qui dentro). Il percorso interattivo (JakeCore) non lo
    # popola ancora - resta lavoro successivo, dichiarato in ROADMAP_EXECUTION.md.
    policy_reason: Optional[str] = None
    duration_ms: Optional[float] = None
    model: Optional[str] = None
    schema_version: int = ACTION_RECEIPT_SCHEMA_VERSION

    def to_json(self) -> str:
        record = {key: value for key, value in asdict(self).items() if value is not None}
        return json.dumps(record, ensure_ascii=False)


_MANDATORY_STRING_FIELDS = (
    "action_id", "trace_id", "intent", "requested_by", "risk_decision", "authorization",
    "result", "idempotency_key",
)


def validate_action_receipt(receipt: ActionReceipt) -> None:
    """F1.1.8: contratto minimo che ogni ricevuta deve rispettare, in un unico punto invece che
    ripetuto in ogni test. Solleva ValueError con il campo incriminato invece di lasciare che una
    ricevuta incompleta finisca silenziosamente nel ledger (append-only: un errore scritto li'
    non si corregge piu', vedi il modulo docstring). Non valuta ancora chi ha CHIAMATO questo
    percorso (F1.1.1/F1.1.6/F1.1.7, migrazione skill-per-skill, restano lavoro successivo): qui
    si controlla solo che l'oggetto ActionReceipt che si sta per scrivere sia ben formato."""
    for field in _MANDATORY_STRING_FIELDS:
        if not getattr(receipt, field):
            raise ValueError(f"ActionReceipt.{field} e' obbligatorio e non puo' essere vuoto")
    if receipt.ts <= 0:
        raise ValueError("ActionReceipt.ts deve essere un timestamp positivo")
    if receipt.verified not in _VERIFICATION_STATUSES:
        raise ValueError(
            f"ActionReceipt.verified={receipt.verified!r} non e' uno stato valido "
            f"({', '.join(_VERIFICATION_STATUSES)})"
        )
    if receipt.error_category not in ERROR_CATEGORIES:
        raise ValueError(
            f"ActionReceipt.error_category={receipt.error_category!r} non e' una categoria valida "
            f"({', '.join(sorted(ERROR_CATEGORIES))})"
        )
    if receipt.policy_reason is not None:
        from core.policy_engine import POLICY_REASONS  # import locale: evita un ciclo a livello di modulo

        if receipt.policy_reason not in POLICY_REASONS:
            raise ValueError(
                f"ActionReceipt.policy_reason={receipt.policy_reason!r} non e' una motivazione valida "
                f"({', '.join(sorted(POLICY_REASONS))})"
            )
    if receipt.schema_version != ACTION_RECEIPT_SCHEMA_VERSION:
        raise ValueError(
            f"ActionReceipt.schema_version={receipt.schema_version!r} non supportata "
            f"(attesa {ACTION_RECEIPT_SCHEMA_VERSION}; nessuna migrazione ancora implementata)"
        )


class ActionLedger:
    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else DEFAULT_LEDGER_PATH

    def record(self, receipt: ActionReceipt, *, private: bool = False) -> None:
        # Stessa policy di core/logger.log_action e core/session_recorder.SessionRecorder:
        # nulla viene scritto in modalita' privata, senza eccezioni per il ledger.
        if private:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(receipt.to_json() + "\n")

    def read_all(self) -> list[dict]:
        if not self._path.is_file():
            return []
        records = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def by_trace_id(self, trace_id: str) -> list[dict]:
        return [record for record in self.read_all() if record.get("trace_id") == trace_id]

    def by_action_id(self, action_id: str) -> Optional[dict]:
        for record in self.read_all():
            if record.get("action_id") == action_id:
                return record
        return None

    def by_idempotency_key(self, idempotency_key: str) -> list[dict]:
        return [record for record in self.read_all() if record.get("idempotency_key") == idempotency_key]

    def duplicate_idempotency_keys(self, within_seconds: float = 60) -> dict[str, list[dict]]:
        """Chiavi di idempotenza comparse piu' di una volta entro `within_seconds` l'una
        dall'altra - un aiuto per l'audit ("questa azione e' partita due volte per errore?"),
        non un'enforcement (vedi idempotency_key_of): raggruppa le ricevute vicine nel tempo per
        la stessa chiave, ignora ripetizioni legittime a distanza di ore/giorni (es. la stessa
        skill con gli stessi parametri usata due volte in momenti scollegati e' normale, due
        volte nello stesso minuto e' piu' probabile un bug)."""
        by_key: dict[str, list[dict]] = {}
        for record in self.read_all():
            key = record.get("idempotency_key")
            if key:
                by_key.setdefault(key, []).append(record)

        duplicates = {}
        for key, records in by_key.items():
            records = sorted(records, key=lambda r: r.get("ts", 0))
            clustered = [
                records[i] for i in range(1, len(records))
                if records[i].get("ts", 0) - records[i - 1].get("ts", 0) <= within_seconds
            ]
            if clustered:
                duplicates[key] = records
        return duplicates
