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


def unprotect(value: str) -> str:
    """Decifra un valore "dpapi:...". Se value non ha il prefisso, lo restituisce cosi' com'e'
    invece di sollevare un errore: puo' essere un valore mai protetto (non ancora migrato, o
    arrivato da JAKE_<CHIAVE> - le variabili d'ambiente restano volutamente in chiaro, sono gia'
    il modo per tenere un segreto fuori dal file su disco, vedi core/config.py) o vuoto."""
    if not is_protected(value):
        return value
    import win32crypt

    encrypted = base64.b64decode(value[len(_PREFIX):])
    _description, decrypted = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
    return decrypted.decode("utf-8")
