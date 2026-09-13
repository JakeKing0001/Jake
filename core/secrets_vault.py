"""Protezione dei segreti a riposo con Windows DPAPI (F1, Trustworthy Agent Core 3.0): prima di
questo modulo, admin_passphrase e home_assistant_token in config/settings.json restavano in
chiaro - chiunque legga quel file (backup, sincronizzazione cloud, un altro utente con accesso al
file) li vede cosi' come sono. DPAPI (Data Protection API di Windows, via win32crypt - gia' una
dipendenza transitiva di pyttsx3, vedi requirements/base.txt) cifra legandosi all'account Windows
corrente: lo stesso blob cifrato non si decifra su un'altra macchina o un altro utente Windows,
senza dover generare, distribuire o custodire noi stessi una chiave separata.

Non e' un vault generico (niente scadenza, rotazione o audit di accesso): e' la protezione minima
"il file non deve essere leggibile da chi non e' te su questo PC", non un sistema di identity/
secrets management completo.

F1.4.1 ("consolidare DPAPI in un SecretsVault con versione e migrazione atomica"): prima di
questa correzione il modulo era solo tre funzioni libere senza alcun tag di versione nel blob
cifrato - un formato futuro diverso (un algoritmo diverso, un cambio di codifica) non avrebbe
avuto modo di distinguersi da quello attuale, ne' di coesistere con blob vecchi gia' su disco.
`SecretsVault` e' la classe reale richiesta dalla roadmap: ogni blob che PRODUCE porta ora un
numero di versione esplicito (`"dpapi:1:<base64>"` invece del vecchio `"dpapi:<base64>"` senza
versione), mentre `unprotect()` continua a leggere ENTRAMBI i formati - un blob vecchio gia'
salvato da un'installazione precedente a questa correzione resta decifrabile per sempre, non
diventa illeggibile solo perche' il codice e' cambiato. `needs_migration()`/`Config.
_migrate_secrets()` usano questo per ricifrare sul posto (con la stessa scrittura ATOMICA gia'
in F1.4.1) un segreto che era gia' protetto ma nel formato legacy, non solo uno ancora in
chiaro - la migrazione a riposo copre quindi sia "mai stato cifrato" sia "cifrato ma in un
formato superato", nello stesso passaggio automatico e silenzioso.

Le funzioni libere is_protected/protect/unprotect restano per compatibilita' con chi le importa
gia' cosi' (vedi tests/test_secrets_vault.py): delegano tutte a un'istanza di default dello
stesso SecretsVault, non una logica duplicata."""
import base64

# Distingue un valore gia' protetto da uno ancora in chiaro (serve alla migrazione: vedi
# core/config.py Config._migrate_secrets), senza dover tentare la decifratura per scoprirlo.
_PREFIX = "dpapi:"

# Formato del blob prima di questa correzione: "dpapi:<base64>", nessun segmento di versione -
# trattato come versione 0 in lettura, mai piu' prodotto in scrittura da questo codice.
_LEGACY_VERSION = 0
CURRENT_VERSION = 1
SUPPORTED_VERSIONS = frozenset({_LEGACY_VERSION, CURRENT_VERSION})


def is_protected(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def _split_payload(value: str) -> tuple[int, str]:
    """Riconosce ENTRAMBI i formati dal payload dopo "dpapi:": "N:<base64>" (versionato, da
    questa correzione in poi) o "<base64>" nudo (legacy, versione 0 implicita). L'alfabeto
    base64 non contiene mai ":", quindi la distinzione e' inequivocabile: se il primo segmento
    prima del primo ":" e' tutto cifre, e' un tag di versione, non dati cifrati."""
    version_str, sep, rest = value.partition(":")
    if sep and version_str.isdigit():
        return int(version_str), rest
    return _LEGACY_VERSION, value


class SecretsVault:
    """Consolida la protezione DPAPI in un oggetto versionato (F1.4.1), invece delle funzioni
    libere senza stato che c'erano prima. `version` e' la versione con cui QUESTA istanza
    protegge (mai quella con cui decifra: unprotect() legge sempre tutte le SUPPORTED_VERSIONS,
    indipendentemente da quale sia stata usata per costruire il blob)."""

    def __init__(self, version: int = CURRENT_VERSION):
        self.version = version

    def protect(self, plaintext: str) -> str:
        """Cifra plaintext per l'utente Windows corrente. Restituisce
        "dpapi:<versione>:<base64>", da salvare al posto del testo in chiaro."""
        import win32crypt

        encrypted = win32crypt.CryptProtectData(plaintext.encode("utf-8"), None, None, None, None, 0)
        return f"{_PREFIX}{self.version}:{base64.b64encode(encrypted).decode('ascii')}"

    def unprotect(self, value: str) -> str | None:
        """Decifra un valore "dpapi:...", in qualunque delle SUPPORTED_VERSIONS sia stato
        prodotto. Se value non ha il prefisso, lo restituisce cosi' com'e' invece di sollevare un
        errore: puo' essere un valore mai protetto (non ancora migrato, o arrivato da
        JAKE_<CHIAVE> - le variabili d'ambiente restano volutamente in chiaro, sono gia' il modo
        per tenere un segreto fuori dal file su disco, vedi core/config.py) o vuoto.

        F1.4.8 ("testare... vault corrotto, profilo Windows differente e backup"): se il valore
        HA il prefisso ma la decifratura fallisce (o la versione non e' tra quelle supportate -
        un blob scritto da una versione FUTURA di Jake, ancora sconosciuta a questo codice),
        restituisce None invece di sollevare. Scenari reali, non ipotetici: (1) il blob e' stato
        cifrato su un profilo Windows o una macchina diversa - DPAPI lo lega all'account che lo
        ha creato (vedi il docstring del modulo), quindi ripristinare config/settings.json da un
        backup su un altro PC o restaurare l'account Windows rende il valore per sempre
        indecifrabile li'; (2) il file e' stato modificato a mano o corrotto (un editor di testo,
        una sincronizzazione interrotta a meta'). Senza questa protezione, `Config.
        get("admin_passphrase")` propagherebbe l'eccezione fino a `JakeCore.__init__` (che
        costruisce `AuthGate` con quel valore), facendo crashare l'AVVIO INTERO di Jake per un
        singolo segreto illeggibile - il chiamante (core/config.py::Config.get) tratta None come
        "segreto mai impostato" invece che come un crash, coerente con "degrado elegante" tra i
        principi non negoziabili di ROADMAP.md. Nessun tipo di eccezione e' garantito in modo
        stabile su tutte le versioni di pywin32 per un blob incompatibile: un except ampio e' la
        scelta deliberata qui, stesso principio gia' usato altrove per un confine che non deve
        mai propagare (vedi core/execution_safety.py::rollback_effect)."""
        if not is_protected(value):
            return value
        version, b64_payload = _split_payload(value[len(_PREFIX):])
        if version not in SUPPORTED_VERSIONS:
            return None
        import win32crypt

        try:
            encrypted = base64.b64decode(b64_payload)
            _description, decrypted = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
            return decrypted.decode("utf-8")
        except Exception:
            return None

    def needs_migration(self, value) -> bool:
        """True se value e' gia' protetto ma NON nel formato versionato corrente di questa
        istanza (F1.4.1, "migrazione atomica") - un blob legacy (versione 0, senza tag esplicito)
        o scritto da una versione precedente di Jake. Falso per un valore in chiaro (quello lo
        gestisce gia' la migrazione esistente in Config._migrate_secrets, protect() lo copre) o
        gia' aggiornato: nessuna doppia scrittura a ogni avvio."""
        if not is_protected(value):
            return False
        version, _ = _split_payload(value[len(_PREFIX):])
        return version != self.version


# Istanza di default: le funzioni libere sotto (compatibilita' con chi le importava prima che
# esistesse questa classe) delegano tutte qui, nessuna logica duplicata.
_default_vault = SecretsVault()


def protect(plaintext: str) -> str:
    return _default_vault.protect(plaintext)


def unprotect(value: str) -> str | None:
    return _default_vault.unprotect(value)
