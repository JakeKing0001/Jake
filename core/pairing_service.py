"""Servizio di pairing per nuovi dispositivi via QR (F1.4.5, fase 4/10 del piano multi-device -
decisione di prodotto esplicita dell'utente, vedi ROADMAP_EXECUTION.md sezione F1.4).

Flusso concordato: Jake Core genera una `PairingChallenge` temporanea -> il payload da
codificare come QR contiene SOLO dati non sensibili (l'id della richiesta, mai un token) -> il
companion la scansiona e la rimanda al core -> il core chiede conferma ESPLICITA sul PC (questo
modulo non la chiede lui stesso - `approve()` presuppone che chi chiama l'abbia gia' ottenuta,
stesso principio "chi chiama deve aver gia' controllato" di `core/execution_safety.py::
decide_automated`) -> solo dopo l'approvazione nasce un `device_id` e viene emessa una
credenziale specifica per quel dispositivo (`core/device_credential_store.py`, fase 2). Un
pairing rifiutato, scaduto o mai iniziato non crea MAI un dispositivo.

Le challenge sono EFFIMERE in memoria, non persistite - una richiesta di pairing non completata
entro `CHALLENGE_TTL_SECONDS` non ha senso sopravviva a un riavvio di Jake, stesso principio gia'
applicato a `core/device_registry.py` (che traccia solo il dispositivo attivo ORA, mai su disco).
Il collegamento vero agli endpoint HTTP di `core/companion_server.py` resta un passo successivo
dichiarato (fasi 6+ del piano), qui solo il servizio."""
import secrets
import threading
import time

from core.device_credential_store import DeviceCredentialStore
from core.device_identity import DeviceCredential, PairingChallenge
from core.logger import get_logger

# F1.4.5: "la challenge scade dopo 5 minuti".
CHALLENGE_TTL_SECONDS = 5 * 60
_CHALLENGE_ID_BYTES = 16
_DEVICE_ID_BYTES = 8


class PairingService:
    def __init__(self, credential_store: DeviceCredentialStore, time_source=time.time):
        self.credential_store = credential_store
        self._time_source = time_source
        self._logger = get_logger()
        self._lock = threading.Lock()
        self._challenges: dict[str, PairingChallenge] = {}

    def start_pairing(self, requested_name: str = "") -> PairingChallenge:
        """Nuova richiesta di pairing. `challenge_id` e' generato con secrets.token_urlsafe, non
        indovinabile: la sicurezza del pairing non viene dalla segretezza del QR (il payload e'
        dichiaratamente non sensibile) ma dal fatto che approvarlo richiede comunque un'azione
        ESPLICITA dell'utente sul PC - l'id non indovinabile impedisce solo che un TERZO possa
        proporsi al posto del companion legittimo indovinando/enumerando id brevi."""
        with self._lock:
            challenge_id = secrets.token_urlsafe(_CHALLENGE_ID_BYTES)
            now = self._time_source()
            challenge = PairingChallenge(
                challenge_id=challenge_id, created_at=now, expires_at=now + CHALLENGE_TTL_SECONDS,
                requested_name=requested_name,
            )
            self._challenges[challenge_id] = challenge
            return challenge

    @staticmethod
    def qr_payload(challenge: PairingChallenge) -> dict:
        """Il payload da codificare nel QR: SOLO `challenge_id`/`expires_at` (F1.4.5, "il payload
        contiene solo dati non sensibili necessari al pairing") - mai un token, mai una
        credenziale, prima ancora che ne esista una da proteggere."""
        return {"challenge_id": challenge.challenge_id, "expires_at": challenge.expires_at}

    def get_challenge(self, challenge_id: str) -> PairingChallenge | None:
        with self._lock:
            return self._challenges.get(challenge_id)

    def approve(self, challenge_id: str, device_name: str = "") -> DeviceCredential | None:
        """None (nessun dispositivo creato) se la challenge non esiste, e' gia' stata
        consumata (F1.4.5, "replay della challenge deve fallire") o e' scaduta - MAI un
        fallback che la accetti comunque. `device_name` sovrascrive, se presente, il nome
        eventualmente gia' noto alla challenge (`requested_name`, impostato da `start_pairing`);
        vuoto lascia quello. Il `device_id` nasce QUI, generato da Jake - mai scelto dal
        chiamante/dal dispositivo stesso (impedisce a un dispositivo di proporre un device_id
        gia' usato da un altro, o di sceglierne uno con un significato speciale)."""
        with self._lock:
            challenge = self._challenges.get(challenge_id)
            if challenge is None or not challenge.is_usable(now=self._time_source()):
                return None
            challenge.used = True
            name = device_name or challenge.requested_name
        device_id = secrets.token_hex(_DEVICE_ID_BYTES)
        self.credential_store.register_device(device_id, name)
        credential = self.credential_store.issue_credential(device_id)
        self._logger.info("Pairing approvato: nuovo dispositivo %s (%s).", device_id, name or "senza nome")
        return credential

    def reject(self, challenge_id: str) -> bool:
        """Vero se c'era davvero una challenge usabile da rifiutare. Consuma comunque la
        challenge (F1.4.5, "un pairing rifiutato non deve creare alcun device" - ma non deve
        nemmeno restare riprovabile all'infinito: rifiutata una volta, resta rifiutata)."""
        with self._lock:
            challenge = self._challenges.get(challenge_id)
            if challenge is None or not challenge.is_usable(now=self._time_source()):
                return False
            challenge.used = True
            return True
