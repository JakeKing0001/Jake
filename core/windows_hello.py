"""Windows Hello per operazioni ADMIN e ad alto impatto (F1, Trustworthy Agent Core 3.0): "la
voce può riconoscere l'utente per comodità, ma non deve essere l'unico fattore di sicurezza" -
vedi la fase F1 in ROADMAP.md. Prima di questo modulo l'unico fattore reale era la passphrase
vocale ripetuta (core/auth_gate.py): chiunque sentisse quella frase (o la leggesse da un log)
poteva ripeterla. Windows Hello aggiunge un fattore che non passa dalla voce - impronta, volto o
PIN del dispositivo - verificato dal sistema operativo, non da Jake.

Usa `Windows.Security.Credentials.UI.UserConsentVerifier` (via winsdk, già una dipendenza -
core/vision/screen.py la usa per l'OCR): l'API minima e corretta per "chiedi all'utente di
verificare la propria presenza con quello che ha già configurato in Windows Hello", non un
intero flusso WebAuthn/FIDO2 con registrazione ed enrollment di credenziali - Jake non deve
gestire chiavi, e' il sistema operativo a farlo.

request_verification_async mostra davvero il prompt nativo di Windows (impronta/volto/PIN) e
aspetta l'utente: e' un'interazione reale sullo schermo, non simulabile ne' automatizzabile nei
test. verify() accetta percio' un parametro _verified_synchronously iniettabile (vedi sotto):
un primo tentativo di questo modulo provava a sostituire winsdk.windows.security.credentials.ui
in sys.modules per i test, ma winsdk usa un proprio meccanismo di import (una proiezione WinRT,
non un modulo Python normale) che non passa in modo affidabile da li' - il test ha finito per
chiamare l'API vera e mostrare un prompt reale sullo schermo dell'utente durante `python -m
unittest`, scoperto A POSTERIORI (non a tavolino) quando il comando e' rimasto bloccato oltre
il timeout. Iniettare l'intera funzione di verifica, invece di provare a intercettare l'import,
rende impossibile per un test toccare anche solo per sbaglio l'API reale."""
import asyncio


def is_available() -> bool:
    """Vero se Windows Hello e' configurato e pronto su questa macchina (impronta/volto/PIN
    registrati, non disattivato da criteri di gruppo). Non mostra nessun prompt: sicura da
    chiamare per davvero anche nei test (vedi tests/test_windows_hello.py)."""
    try:
        import winsdk.windows.security.credentials.ui as ui

        async def _check():
            return await ui.UserConsentVerifier.check_availability_async()

        return asyncio.run(_check()) == ui.UserConsentVerifierAvailability.AVAILABLE
    except Exception:
        # Degrado elegante (principio della roadmap): se winsdk manca, l'API non e' disponibile
        # su questa build di Windows, o qualunque altra cosa va storta, Windows Hello e'
        # semplicemente "non disponibile" - mai un crash che blocchi un'azione ADMIN a meta'.
        return False


def _verified_synchronously(reason: str) -> bool:
    """La chiamata vera: mostra il prompt nativo di Windows Hello con `reason` come messaggio e
    aspetta l'utente. Separata da verify() apposta - vedi il modulo sul perche' un test deve
    poter sostituire QUESTA funzione (mai chiamata nella suite), non provare a intercettare
    l'import di winsdk al suo interno."""
    import winsdk.windows.security.credentials.ui as ui

    async def _request():
        return await ui.UserConsentVerifier.request_verification_async(reason)

    return asyncio.run(_request()) == ui.UserConsentVerificationResult.VERIFIED


def verify(reason: str, _verified_synchronously=_verified_synchronously) -> bool:
    """True solo se la verifica e' riuscita davvero (impronta/volto/PIN corretti). False per
    qualunque altro esito - annullato dall'utente, nessun metodo configurato, criteri di gruppo,
    dispositivo occupato, troppi tentativi falliti, o un errore imprevisto: dal punto di vista di
    chi chiama sono tutti "non autorizzato", la distinzione fine non serve qui."""
    try:
        return _verified_synchronously(reason)
    except Exception:
        return False
