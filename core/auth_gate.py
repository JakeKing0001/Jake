"""Autenticazione per le azioni piu' rischiose (v5.5, Identity & Authentication), l'ultimo
gradino del kernel di permessi (v5.4, vedi core/risk.py: READ_ONLY/LOCAL_REVERSIBLE/
EXTERNAL_ACTION/DESTRUCTIVE/ADMIN -> ALLOW/ALLOW/ALLOW/CONFIRM/REQUIRE_AUTH).

Jake e' un assistente locale mono-utente, non un sistema multi-tenant: non serve un vero login
con sessioni, serve solo un modo per verificare che sia davvero il proprietario a chiedere
un'azione ADMIN (spegnere il PC, eseguire un comando, far installare a Jake una capacita' che si
e' scritto da solo) e non, per esempio, chiunque altro sia entro udito del microfono. Una
passphrase configurata dall'utente, da ripetere per confermare, e' il meccanismo piu' semplice
che risolve questo senza inventare un intero sistema di identita'.

Deliberatamente OPT-IN: se non e' mai stata configurata una passphrase, enabled e' False e le
azioni ADMIN restano protette solo dalla conferma si'/no gia' esistente (v3.2), esattamente come
prima di questa fase - nessuna regressione per chi non ha mai attivato nulla."""


class AuthGate:
    def __init__(self, passphrase: str = None):
        self.passphrase = passphrase.strip() if passphrase else None

    @property
    def enabled(self) -> bool:
        return bool(self.passphrase)

    def check(self, attempt: str) -> bool:
        """Vero se 'attempt' corrisponde alla passphrase configurata. Sempre falso se
        l'autenticazione non e' attiva (enabled == False): non c'e' nulla con cui corrispondere."""
        if not self.enabled:
            return False
        return (attempt or "").strip() == self.passphrase
