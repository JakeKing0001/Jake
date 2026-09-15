"""Adapter di autenticazione (F1.4.4, fase 9/10 del piano multi-device - decisione di prodotto
esplicita dell'utente, vedi ROADMAP_EXECUTION.md sezione F1.4).

"Non sostituire Windows Hello. Usa: Windows Hello come autenticazione locale primaria per azioni
ADMIN sul PC; WebAuthn/passkey come autenticazione opzionale per companion/mobile e operazioni
cross-device ad alto impatto. Implementare dietro un adapter, non direttamente nel core."

Prima di questo modulo, `core/auth_gate.py::AuthGate` chiamava `core.windows_hello.verify()`
tramite un callable grezzo iniettato (`windows_hello_verify`) - funzionava, ma legava
`AuthGate` a QUELLA implementazione specifica, non a un contratto. `AuthProvider` e' quel
contratto: "sei disponibile ADESSO" + "verifica l'utente per questa azione", uguale per Windows
Hello e per una futura passkey, cosi' un chiamante (`AuthGate`, o un domani un endpoint companion
che chiede "quale fattore posso usare da qui") non deve sapere COME un provider verifica,
solo SE.

`PasskeyProvider` e' dichiaratamente NON un vero flusso WebAuthn/FIDO2 - "per ora va bene
costruire interfacce, contratti e integrazione minima verificabile; non serve creare un'app
mobile completa per chiudere la parte core" (decisione esplicita dell'utente). `is_available()`
e' onestamente `False` finche' non esiste un vero registro di passkey per dispositivo da
interrogare: dichiarare disponibile un fattore che non puo' verificare nulla per davvero
sarebbe peggio di dichiararlo assente, stesso principio "mai un valore inventato" gia' seguito
altrove in questa sessione (`core/action_contracts.py::effect_class_of`)."""
from abc import ABC, abstractmethod


class AuthProvider(ABC):
    """Un fattore di autenticazione non vocale, verificato da qualcosa ESTERNO a Jake (il
    sistema operativo per Windows Hello, un authenticator FIDO2 per una passkey) - mai Jake
    stesso, che non deve mai essere l'unico giudice della propria autorizzazione."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Vero se questo fattore e' utilizzabile ADESSO su questa macchina/canale - non mostra
        mai un prompt, sicuro da chiamare per decidere quali opzioni offrire."""
        raise NotImplementedError

    @abstractmethod
    def verify(self, reason: str) -> bool:
        """Vero solo se l'utente si e' verificato per davvero con questo fattore, per l'azione
        descritta da `reason`. Falso per qualunque altro esito (annullato, non disponibile,
        errore) - dal punto di vista di chi chiama sono tutti "non autorizzato"."""
        raise NotImplementedError


class WindowsHelloProvider(AuthProvider):
    """Avvolge `core/windows_hello.py` (F1.4.3, gia' esistente) - nessuna logica nuova, solo
    l'adapter richiesto dalla specifica. Import pigro delle funzioni reali dentro ogni metodo
    (non in cima al modulo): stesso motivo gia' documentato in `core/windows_hello.py` e
    `core/auth_gate.py` - non toccare `winsdk` affatto quando Windows Hello non serve/non e'
    configurato."""

    name = "windows_hello"

    def is_available(self) -> bool:
        from core import windows_hello

        return windows_hello.is_available()

    def verify(self, reason: str) -> bool:
        from core import windows_hello

        return windows_hello.verify(reason)


class PasskeyProvider(AuthProvider):
    """WebAuthn/passkey per companion/mobile (F1.4.4) - vedi il docstring del modulo sul perche'
    resta onestamente inerte per ora: nessun registro di passkey per dispositivo esiste ancora,
    nessuna app companion sa parlare WebAuthn. Un punto di estensione pronto, non una promessa
    non mantenuta: `is_available()` non mente mai dicendo "si'" per poi fallire `verify()`."""

    name = "passkey"

    def is_available(self) -> bool:
        return False

    def verify(self, reason: str) -> bool:
        return False
