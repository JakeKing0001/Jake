"""Protezione dei segreti a riposo con Windows DPAPI (F1, Trustworthy Agent Core 3.0): prima di
questo modulo, admin_passphrase e home_assistant_token in config/settings.json restavano in
chiaro - chiunque legga quel file (backup, sincronizzazione cloud, un altro utente con accesso al
file) li vede cosi' come sono. DPAPI (Data Protection API di Windows, via win32crypt - gia' una
dipendenza transitiva di pyttsx3, vedi requirements/base.txt) cifra legandosi all'account Windows
corrente: lo stesso blob cifrato non si decifra su un'altra macchina o un altro utente Windows,
senza dover generare, distribuire o custodire noi stessi una chiave separata.

Non e' un vault generico (niente scadenza, rotazione o audit di accesso): e' la protezione minima
"il file non deve essere leggibile da chi non e' te su questo PC", non un sistema di identity/
secrets management completo."""
import base64

# Distingue un valore gia' protetto da uno ancora in chiaro (serve alla migrazione: vedi
# core/config.py Config._migrate_secrets), senza dover tentare la decifratura per scoprirlo.
_PREFIX = "dpapi:"


def is_protected(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def protect(plaintext: str) -> str:
    """Cifra plaintext per l'utente Windows corrente. Restituisce "dpapi:<base64>", da salvare
    al posto del testo in chiaro."""
    import win32crypt

    encrypted = win32crypt.CryptProtectData(plaintext.encode("utf-8"), None, None, None, None, 0)
    return _PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect(value: str) -> str | None:
    """Decifra un valore "dpapi:...". Se value non ha il prefisso, lo restituisce cosi' com'e'
    invece di sollevare un errore: puo' essere un valore mai protetto (non ancora migrato, o
    arrivato da JAKE_<CHIAVE> - le variabili d'ambiente restano volutamente in chiaro, sono gia'
    il modo per tenere un segreto fuori dal file su disco, vedi core/config.py) o vuoto.

    F1.4.8 ("testare... vault corrotto, profilo Windows differente e backup"): se il valore HA
    il prefisso ma la decifratura fallisce, restituisce None invece di sollevare. Due scenari
    reali, non ipotetici: (1) il blob e' stato cifrato su un profilo Windows o una macchina
    diversa - DPAPI lo lega all'account che lo ha creato (vedi il docstring del modulo), quindi
    ripristinare config/settings.json da un backup su un altro PC o restaurare l'account Windows
    rende il valore per sempre indecifrabile li'; (2) il file e' stato modificato a mano o
    corrotto (un editor di testo, una sincronizzazione interrotta a meta'). Senza questa
    protezione, `Config.get("admin_passphrase")` propagherebbe l'eccezione fino a
    `JakeCore.__init__` (che costruisce `AuthGate` con quel valore), facendo crashare l'AVVIO
    INTERO di Jake per un singolo segreto illeggibile - il chiamante (core/config.py::Config.get)
    tratta None come "segreto mai impostato" invece che come un crash, coerente con "degrado
    elegante" tra i principi non negoziabili di ROADMAP.md. Nessun tipo di eccezione e'
    garantito in modo stabile su tutte le versioni di pywin32 per un blob incompatibile: un
    except ampio e' la scelta deliberata qui, stesso principio gia' usato altrove per un confine
    che non deve mai propagare (vedi core/execution_safety.py::rollback_effect)."""
    if not is_protected(value):
        return value
    import win32crypt

    try:
        encrypted = base64.b64decode(value[len(_PREFIX):])
        _description, decrypted = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
        return decrypted.decode("utf-8")
    except Exception:
        return None
