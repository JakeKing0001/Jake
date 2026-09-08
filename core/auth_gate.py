"""Autenticazione per le azioni piu' rischiose (v5.5, Identity & Authentication), l'ultimo
gradino del kernel di permessi (v5.4, vedi core/risk.py: READ_ONLY/LOCAL_REVERSIBLE/
EXTERNAL_ACTION/DESTRUCTIVE/ADMIN -> ALLOW/ALLOW/ALLOW/CONFIRM/REQUIRE_AUTH).

Jake e' un assistente locale mono-utente, non un sistema multi-tenant: non serve un vero login
con sessioni, serve solo un modo per verificare che sia davvero il proprietario a chiedere
un'azione ADMIN (spegnere il PC, eseguire un comando, far installare a Jake una capacita' che si
e' scritto da solo) e non, per esempio, chiunque altro sia entro udito del microfono.

Due fattori indipendenti, non uno sostituto dell'altro:
- una passphrase configurata dall'utente, da ripetere a voce/testo per confermare;
- Windows Hello (F1, core/windows_hello.py: impronta/volto/PIN del dispositivo, verificato dal
  sistema operativo, non da Jake) - "la voce può riconoscere l'utente per comodità, ma non deve
  essere l'unico fattore di sicurezza", vedi la fase F1 in ROADMAP.md. Tentato PRIMA della
  passphrase quando entrambi sono attivi (vedi JakeCore._resolve_and_execute): e' piu' forte (non
  passa dalla voce, quindi non e' intercettabile ne' ripetibile da chi sente solo l'audio) e non
  richiede un turno di conversazione in piu' per essere verificato.

Deliberatamente OPT-IN: se non e' mai stata configurata una passphrase ne' Windows Hello,
enabled e' False e le azioni ADMIN restano protette solo dalla conferma si'/no gia' esistente
(v3.2), esattamente come prima di questa fase - nessuna regressione per chi non ha mai attivato
nulla."""


class AuthGate:
    def __init__(self, passphrase: str = None, windows_hello_enabled: bool = False, windows_hello_verify=None):
        self.passphrase = passphrase.strip() if passphrase else None
        self.windows_hello_enabled = bool(windows_hello_enabled)
        # Iniettabile per i test (vedi core/windows_hello.py sul perche' verify() non deve MAI
        # essere lasciata chiamare l'API vera in un test automatico): di default None, risolto
        # pigramente alla prima chiamata reale cosi' importare questo modulo non importa mai
        # winsdk quando Windows Hello e' disattivato (il caso comune).
        self._windows_hello_verify = windows_hello_verify

    @property
    def enabled(self) -> bool:
        return bool(self.passphrase) or self.windows_hello_enabled

    def check(self, attempt: str) -> bool:
        """Vero se 'attempt' corrisponde alla passphrase configurata. Sempre falso se non e'
        stata configurata: non c'e' nulla con cui corrispondere."""
        if not self.passphrase:
            return False
        return (attempt or "").strip() == self.passphrase

    def verify_with_windows_hello(self, reason: str) -> bool:
        """Mostra il prompt nativo di Windows Hello (se attivo) e restituisce se ha verificato
        l'utente per davvero. False immediato, senza nessuna chiamata, se windows_hello_enabled
        e' spento - non prova nemmeno a controllare se e' disponibile su questa macchina."""
        if not self.windows_hello_enabled:
            return False
        if self._windows_hello_verify is None:
            from core.windows_hello import verify as _real_verify

            self._windows_hello_verify = _real_verify
        return self._windows_hello_verify(reason)
