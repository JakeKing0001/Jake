"""Storage persistente per identita' e credenziali dei dispositivi (F1.4.5/F1.4.6, fase 2/10 del
piano multi-device - decisione di prodotto esplicita dell'utente, vedi ROADMAP_EXECUTION.md
sezione F1.4 per il testo completo e l'ordine concordato).

`core/device_identity.py` (fase 1) definisce solo i CONTRATTI (DeviceIdentity/DeviceCredential/
PairingChallenge); questo modulo e' il primo a persisterli per davvero, sostituendo il singolo
`companion_token` globale condiviso da tutti i dispositivi (core/config.py) con una credenziale
PER dispositivo, revocabile e ruotabile singolarmente.

SQLite (data/jake_devices.db, un file dedicato - non condiviso con data/jake_memory.db: le
credenziali sono dati di sicurezza, non conversazionali, separarli evita che uno strumento che
sfoglia la memoria di Jake incappi per sbagliato in token cifrati). Stesso schema di
sincronizzazione (RLock, check_same_thread=False) gia' usato da core/reminder_manager.py/core/
todo_manager.py: core/companion_server.py gira su ThreadingHTTPServer, ogni richiesta HTTP sul
proprio thread, quindi verify_token()/issue_credential()/revoke() possono arrivare in
concorrenza reale, non solo in teoria (F1.8.2/F1.8.7).

Il token e' cifrato a riposo con lo STESSO SecretsVault/DPAPI gia' usato per admin_passphrase/
home_assistant_token (core/config.py) - nessuna seconda implementazione di cifratura. La verifica
di un token presentato NON ricifra il valore per confrontarlo (CryptProtectData non e'
deterministico: due cifrature dello stesso testo in chiaro possono produrre byte diversi, un
confronto sul ciphertext darebbe sempre falso), decifra invece ogni credenziale nota e confronta
in chiaro con `hmac.compare_digest` (stesso principio a tempo costante gia' usato da
core/auth_gate.py::AuthGate.check) - un confronto per dispositivo noto, non indicizzato: per il
numero di dispositivi personali attesi (una manciata, non migliaia) e' la scelta piu' semplice
che resta corretta, non un'ottimizzazione prematura.

Nota sullo stato REVOKED (core/device_identity.py::DeviceStatus): la specifica di prodotto dice
letteralmente "se il token scade O VIENE REVOCATO, il dispositivo deve tornare nello stato
PAIRING_REQUIRED" - non uno stato REVOKED separato mostrato al dispositivo stesso (che deve solo
sapere "devo ripetere il pairing", niente altro). REVOKED resta nell'enum per il ledger/l'audit
(DeviceCredential.revoked_at distingue GIA' "mai stata revocata" da "revocata a questo istante" -
un revoke lascia comunque traccia di COME un dispositivo e' arrivato a PAIRING_REQUIRED, letta da
chi consulta la credenziale, anche se lo status pubblico del dispositivo torna PAIRING_REQUIRED
come richiesto)."""
import hmac
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from core.device_identity import DeviceCredential, DeviceIdentity, DeviceStatus
from core.logger import get_logger
from core.secrets_vault import SecretsVault

# F1.4.6 ("la rotazione automatica deve avvenire ogni 90 giorni"): TTL di default per una
# credenziale appena emessa o ruotata - un chiamante puo' comunque passare un ttl_seconds diverso
# (usato dai test per non dover aspettare 90 giorni veri), il default di produzione resta questo.
DEFAULT_CREDENTIAL_TTL_SECONDS = 90 * 24 * 3600

# secrets.token_urlsafe(32): 256 bit di entropia, lo stesso ordine di grandezza gia' raccomandato
# dalla documentazione standard di Python per token di sessione/API - primo token generato da
# Jake stesso in questo progetto (companion_token esistente e' scelto e incollato dall'utente).
_TOKEN_BYTES = 32


class DeviceCredentialStore:
    DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_devices.db"

    def __init__(
        self, db_path: Path | None = None, vault: SecretsVault | None = None,
        time_source=time.time,
    ):
        self.db_path = Path(db_path) if db_path else self.DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._vault = vault or SecretsVault()
        self._time_source = time_source
        self._logger = get_logger()
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pairing_required',
                created_at REAL NOT NULL,
                last_seen_at REAL
            );
            CREATE TABLE IF NOT EXISTS device_credentials (
                device_id TEXT PRIMARY KEY REFERENCES devices(device_id),
                token_protected TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked_at REAL
            );
            -- F7.1.3: capability per dispositivo PERSISTENTI (prima solo in memoria: un riavvio restituiva a un
            -- dispositivo limitato i permessi di default).
            CREATE TABLE IF NOT EXISTS device_capabilities (
                device_id TEXT PRIMARY KEY REFERENCES devices(device_id),
                classes TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        self._connection.commit()

    # ---- dispositivi -------------------------------------------------------------------

    def register_device(self, device_id: str, name: str = "") -> DeviceIdentity:
        """Registra un dispositivo nuovo (status iniziale PAIRING_REQUIRED, nessuna credenziale)
        o aggiorna il nome di uno gia' noto - idempotente, stesso principio di
        DeviceRegistry.register() (core/device_registry.py): un secondo register() con lo stesso
        device_id non deve mai far perdere lo stato/la credenziale gia' esistenti."""
        with self._lock:
            now = self._time_source()
            row = self._connection.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO devices (device_id, name, status, created_at) VALUES (?, ?, ?, ?)",
                    (device_id, name, DeviceStatus.PAIRING_REQUIRED.value, now),
                )
                self._connection.commit()
                return DeviceIdentity(device_id=device_id, name=name, created_at=now)
            if name and name != row["name"]:
                self._connection.execute(
                    "UPDATE devices SET name = ? WHERE device_id = ?", (name, device_id),
                )
                self._connection.commit()
            return self._row_to_identity(dict(row) | {"name": name or row["name"]})

    def get_device(self, device_id: str) -> DeviceIdentity | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,),
            ).fetchone()
            return self._row_to_identity(dict(row)) if row else None

    def list_devices(self) -> list[DeviceIdentity]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM devices ORDER BY created_at ASC").fetchall()
            return [self._row_to_identity(dict(row)) for row in rows]

    def touch_last_seen(self, device_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE devices SET last_seen_at = ? WHERE device_id = ?",
                (self._time_source(), device_id),
            )
            self._connection.commit()

    @staticmethod
    def _row_to_identity(row: dict) -> DeviceIdentity:
        return DeviceIdentity(
            device_id=row["device_id"], name=row["name"] or "", status=row["status"],
            created_at=row["created_at"], last_seen_at=row["last_seen_at"],
        )

    def _set_status_locked(self, device_id: str, status: DeviceStatus) -> None:
        self._connection.execute(
            "UPDATE devices SET status = ? WHERE device_id = ?", (status.value, device_id),
        )
        self._connection.commit()

    # ---- credenziali ---------------------------------------------------------------------

    def issue_credential(
        self, device_id: str, *, ttl_seconds: float = DEFAULT_CREDENTIAL_TTL_SECONDS,
    ) -> DeviceCredential:
        """Emette (o RUOTA, se il dispositivo ne aveva gia' una) una nuova credenziale per
        device_id, e porta il dispositivo ad ACTIVE. Il token in CHIARO viene restituito solo
        qui, una volta sola: da questo punto in poi lo store conserva solo la versione cifrata
        con SecretsVault - chi chiama deve consegnarlo al dispositivo ORA, non puo' recuperarlo
        una seconda volta (stesso principio di un bearer token qualsiasi: perso, si ruota di
        nuovo, non si "ri-legge"). F1.4.6 ("ruotato SENZA revocare gli altri"): tocca solo la riga
        di QUESTO device_id, mai le credenziali di altri dispositivi."""
        with self._lock:
            self.register_device(device_id)  # idempotente: no-op se gia' noto
            token = secrets.token_urlsafe(_TOKEN_BYTES)
            now = self._time_source()
            expires_at = now + ttl_seconds
            protected = self._vault.protect(token)
            self._connection.execute(
                "INSERT INTO device_credentials (device_id, token_protected, issued_at, expires_at, revoked_at) "
                "VALUES (?, ?, ?, ?, NULL) "
                "ON CONFLICT(device_id) DO UPDATE SET "
                "token_protected = excluded.token_protected, issued_at = excluded.issued_at, "
                "expires_at = excluded.expires_at, revoked_at = NULL",
                (device_id, protected, now, expires_at),
            )
            self._set_status_locked(device_id, DeviceStatus.ACTIVE)
            return DeviceCredential(device_id=device_id, token=token, issued_at=now, expires_at=expires_at)

    def rotate_credential(
        self, device_id: str, *, ttl_seconds: float = DEFAULT_CREDENTIAL_TTL_SECONDS,
    ) -> DeviceCredential | None:
        """Alias esplicito di issue_credential() per i chiamanti per cui "ruota" comunica meglio
        l'intento di "sostituisci la credenziale di un dispositivo GIA' noto" - None se
        device_id non e' mai stato registrato (niente da ruotare), invece di crearlo al volo in
        modo implicito, a differenza di issue_credential() che lo fa deliberatamente per un primo
        pairing."""
        with self._lock:
            if self.get_device(device_id) is None:
                return None
            return self.issue_credential(device_id, ttl_seconds=ttl_seconds)

    def revoke(self, device_id: str) -> bool:
        """Revoca la credenziale attiva di device_id e lo riporta a PAIRING_REQUIRED (F1.4.6).
        Vero se c'era davvero una credenziale non gia' revocata da invalidare."""
        with self._lock:
            row = self._connection.execute(
                "SELECT revoked_at FROM device_credentials WHERE device_id = ?", (device_id,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                return False
            self._connection.execute(
                "UPDATE device_credentials SET revoked_at = ? WHERE device_id = ?",
                (self._time_source(), device_id),
            )
            self._set_status_locked(device_id, DeviceStatus.PAIRING_REQUIRED)
            self._logger.warning("Credenziale revocata per il dispositivo %s.", device_id)
            return True

    def verify_token(self, token: str) -> str | None:
        """device_id del dispositivo a cui appartiene `token`, se e solo se la sua credenziale
        e' valida ADESSO (ne' revocata ne' scaduta) - None altrimenti, MAI un fallback al vecchio
        companion_token globale (F1.4.6: "non deve esistere fallback automatico a un token
        globale" - quella migrazione e' un passo successivo dichiarato, non affrontato qui).
        Una credenziale trovata ma scaduta riporta il dispositivo a PAIRING_REQUIRED seduta
        stante (non serve aspettare uno scheduler): la prossima richiesta con quel token vede
        gia' lo stato corretto."""
        if not token:
            return None
        with self._lock:
            rows = self._connection.execute(
                "SELECT device_id, token_protected, issued_at, expires_at, revoked_at FROM device_credentials",
            ).fetchall()
            now = self._time_source()
            for row in rows:
                stored_plaintext = self._vault.unprotect(row["token_protected"])
                if stored_plaintext is None:
                    continue  # vault corrotto/profilo diverso per QUESTA riga: mai un crash, vedi F1.4.8
                if not hmac.compare_digest(token, stored_plaintext):
                    continue
                credential = DeviceCredential(
                    device_id=row["device_id"], token=stored_plaintext, issued_at=row["issued_at"],
                    expires_at=row["expires_at"], revoked_at=row["revoked_at"],
                )
                if not credential.is_valid(now=now):
                    self._set_status_locked(row["device_id"], DeviceStatus.PAIRING_REQUIRED)
                    return None
                return row["device_id"]
            return None

    def active_credential_issued_at(self, device_id: str) -> float | None:
        """Quando e' stata emessa la credenziale ANCORA valida di device_id (None se revocata, scaduta o assente).
        Controllo economico (nessuna decifratura) per chi ha gia' autenticato il dispositivo e deve sapere se lo e'
        ancora: una connessione aperta (lo stream SSE) confronta questo valore con quello del momento dell'accesso,
        quindi si accorge anche di una rotazione (nuova credenziale = la vecchia non vale piu')."""
        with self._lock:
            row = self._connection.execute(
                "SELECT issued_at, expires_at, revoked_at FROM device_credentials WHERE device_id = ?", (device_id,),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if row["expires_at"] is not None and row["expires_at"] <= self._time_source():
            return None
        return row["issued_at"]

    def set_capabilities(self, device_id: str, classes) -> None:
        import json

        with self._lock:
            self._connection.execute(
                "INSERT INTO device_capabilities (device_id, classes, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET classes = excluded.classes, updated_at = excluded.updated_at",
                (device_id, json.dumps(sorted(str(c) for c in classes)), self._time_source()),
            )
            self._connection.commit()

    def get_capabilities(self, device_id: str) -> list[str] | None:
        import json

        with self._lock:
            row = self._connection.execute(
                "SELECT classes FROM device_capabilities WHERE device_id = ?", (device_id,)).fetchone()
        if row is None:
            return None
        try:
            classes = json.loads(row["classes"])
        except ValueError:
            return []  # riga corrotta: nessun permesso invece dei default (fail-closed)
        return [str(c) for c in classes] if isinstance(classes, list) else []

    def close(self) -> None:
        with self._lock:
            self._connection.close()
