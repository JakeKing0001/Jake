"""Identita', credenziali e pairing per-dispositivo (F1.4.4-F1.4.6/F1.8.1, multi-device -
decisione di prodotto esplicita dell'utente: vedi ROADMAP_EXECUTION.md, sezione F1.4, per il
testo completo della specifica e l'ordine di implementazione concordato).

Prima di questo modulo, l'UNICA nozione di "dispositivo" in produzione era
`core/device_registry.py::DeviceRegistry`: puro stato EFFIMERO in memoria (mai persistito, mai
autenticato per davvero) che risponde solo alla domanda "chi e' il dispositivo attivo ADESSO"
per l'handoff vocale/HUD - non "chi e' autorizzato a parlare con Jake". L'autenticazione era un
unico `companion_token` globale condiviso da OGNI dispositivo companion (`core/config.py`,
`core/companion_server.py`), senza modo di revocare o ruotare un singolo dispositivo senza
invalidare tutti gli altri.

Questo modulo introduce i CONTRATTI DATI per la parte persistente/di sicurezza (identita' del
dispositivo, la sua credenziale, una richiesta di pairing in corso) - deliberatamente separati da
`DeviceRegistry`, che resta il registro EFFIMERO dell'handoff vocale, una responsabilita' diversa
(chi parla ora, non chi e' autorizzato). Lo storage persistente vero (`core/device_credential_
store.py`) e il collegamento a `companion_server.py` sono passi successivi, non affrontati qui -
stesso principio "prima il contratto, poi l'adozione" gia' seguito per `ActionProposal` in
`core/action_contracts.py` (F1.1.2 prima di F1.1.6/F1.1.7), che questo modulo rispecchia
deliberatamente nello stile (dataclass + `validate_*()` che solleva `ValueError` sul campo
incriminato, mai un'eccezione generica)."""
import time
from dataclasses import dataclass
from enum import Enum


class DeviceStatus(str, Enum):
    """Tre soli stati, non un'enumerazione libera (stesso principio di EFFECT_CLASSES in
    core/action_contracts.py): PAIRING_REQUIRED e' lo stato di partenza E quello di ritorno dopo
    una revoca o una scadenza (mai uno stato "scaduto" separato - un dispositivo scaduto deve
    ripetere il pairing esattamente come uno mai accoppiato, nessuna distinzione utile a valle)."""

    PAIRING_REQUIRED = "pairing_required"
    ACTIVE = "active"
    REVOKED = "revoked"


DEVICE_STATUSES = frozenset({status.value for status in DeviceStatus})


@dataclass
class DeviceIdentity:
    """Un dispositivo conosciuto da Jake, indipendentemente dal fatto che abbia in questo
    momento una credenziale valida - `status` riflette SOLO se il dispositivo puo' autenticarsi
    ORA (deriva dalla credenziale associata, non e' un campo indipendente che potrebbe
    disallinearsi: vedi `core/device_credential_store.py` per chi lo tiene sincronizzato)."""

    device_id: str
    name: str = ""
    status: str = DeviceStatus.PAIRING_REQUIRED.value
    created_at: float = 0.0
    last_seen_at: float | None = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = time.time()


def validate_device_identity(identity: DeviceIdentity) -> None:
    if not identity.device_id:
        raise ValueError("DeviceIdentity.device_id e' obbligatorio e non puo' essere vuoto")
    if identity.status not in DEVICE_STATUSES:
        raise ValueError(
            f"DeviceIdentity.status={identity.status!r} non e' uno stato valido "
            f"({', '.join(sorted(DEVICE_STATUSES))})"
        )
    if identity.created_at <= 0:
        raise ValueError("DeviceIdentity.created_at deve essere un timestamp positivo")


@dataclass
class DeviceCredential:
    """La credenziale bearer di UN dispositivo (F1.4.6: "ogni dispositivo ha una propria
    credenziale/token... revocabile singolarmente... ruotato senza revocare gli altri"). `token`
    e' il segreto in CHIARO solo nell'istante in cui viene emesso/restituito al chiamante - lo
    store persistente (core/device_credential_store.py) lo cifra a riposo con lo stesso
    SecretsVault/DPAPI gia' usato per admin_passphrase/home_assistant_token (core/config.py), non
    lo conserva mai in chiaro su disco. Nessun campo booleano "expired" separato: `is_expired()`
    lo calcola sempre da `expires_at`, cosi' un orologio letto in due punti diversi non puo' mai
    disallinearsi da se stesso (stesso principio di UndoDescriptor.is_expired in
    core/action_contracts.py, che questa classe rispecchia deliberatamente)."""

    device_id: str
    token: str
    issued_at: float = 0.0
    expires_at: float = 0.0
    revoked_at: float | None = None

    def __post_init__(self) -> None:
        if not self.issued_at:
            self.issued_at = time.time()

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def is_valid(self, *, now: float | None = None) -> bool:
        """Falso se revocata O scaduta - un dispositivo con una credenziale non valida deve
        tornare a PAIRING_REQUIRED (F1.4.6), mai un fallback silenzioso a un token diverso."""
        return not self.is_revoked() and not self.is_expired(now=now)


def validate_device_credential(credential: DeviceCredential) -> None:
    if not credential.device_id:
        raise ValueError("DeviceCredential.device_id e' obbligatorio e non puo' essere vuoto")
    if not credential.token:
        raise ValueError("DeviceCredential.token e' obbligatorio e non puo' essere vuoto")
    if credential.issued_at <= 0:
        raise ValueError("DeviceCredential.issued_at deve essere un timestamp positivo")
    if credential.expires_at <= credential.issued_at:
        raise ValueError("DeviceCredential.expires_at deve essere successivo a issued_at")


@dataclass
class PairingChallenge:
    """Una richiesta di pairing in corso per un NUOVO dispositivo (F1.4.5). `challenge_id` e' il
    payload codificato nel QR (dati non sensibili: nessun token, nessuna credenziale - solo un
    identificativo casuale della richiesta stessa). `used`/`is_expired()` insieme impediscono un
    replay: una challenge gia' consumata (pairing approvato O rifiutato) o scaduta non puo' mai
    produrre un secondo dispositivo, stesso schema is_expired/is_usable di UndoDescriptor sopra."""

    challenge_id: str
    created_at: float = 0.0
    expires_at: float = 0.0
    used: bool = False
    requested_name: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = time.time()

    def is_expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def is_usable(self, *, now: float | None = None) -> bool:
        return not self.used and not self.is_expired(now=now)


def validate_pairing_challenge(challenge: PairingChallenge) -> None:
    if not challenge.challenge_id:
        raise ValueError("PairingChallenge.challenge_id e' obbligatorio e non puo' essere vuoto")
    if challenge.created_at <= 0:
        raise ValueError("PairingChallenge.created_at deve essere un timestamp positivo")
    if challenge.expires_at <= challenge.created_at:
        raise ValueError("PairingChallenge.expires_at deve essere successivo a created_at")
