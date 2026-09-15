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
nulla.

F1: check() confrontava la passphrase con `==`, un confronto stringa-per-stringa che si ferma al
primo carattere diverso - un canale laterale temporale che permetterebbe in teoria di indovinare
la passphrase un carattere alla volta misurando quanto impiega ogni tentativo a fallire, invece di
doverla indovinare per intero. Lo stesso identico principio era gia' applicato correttamente
altrove in questo progetto per un confronto di segreto (`core/companion_server.py::
_is_authorized`, token del companion server), ma non qui, per la passphrase amministrativa - la
protezione piu' importante del sistema (ADMIN: spegnimento, comandi da terminale, installazione di
skill scritte da Jake stesso). Corretto con `hmac.compare_digest`, a tempo costante rispetto al
CONTENUTO confrontato (non alla lunghezza, che resta osservabile per costruzione - vedi il
docstring di `hmac.compare_digest`).

F1.4.3 (rate limiting/lockout, "mantenere fallback... auditato"): esplicitamente dichiarato "non
ancora affrontato" quando il resto del fallback e' stato irrobustito sopra - "non richiesto
esplicitamente da quel punto della roadmap, non aggiunto per restare in un incremento
verificabile". Investigato prima di implementarlo: `JakeCore._handle_confirmation()` gia' cancella
l'intera azione ADMIN dopo UN SOLO tentativo sbagliato ("niente tentativi ripetuti in loop",
commento gia' presente li') - un attaccante non puo' quindi ritentare la STESSA richiesta di
conferma in ciclo, ma PUO' comunque far ripartire da capo una nuova azione ADMIN (es. via l'API
companion, senza alcun limite di frequenza a nessun livello) e ritentare una passphrase diversa a
ogni giro - piu' lento di un classico ciclo "tenta password", ma comunque scriptabile senza
throttling. `check()` ora conta i tentativi falliti CONSECUTIVI (azzerati da un successo) e,
raggiunta una soglia, blocca ulteriori tentativi per un periodo fisso - valori scelti come
compromesso di prodotto (non richiesti da un'analisi di sicurezza formale): abbastanza tentativi
da tollerare un errore di battitura genuino, abbastanza pochi da rendere impraticabile un
tentativo automatizzato. Durante il blocco, `check()` ritorna sempre False SENZA nemmeno
confrontare (anche una passphrase corretta viene rifiutata finche' il blocco non scade) - un
lockout che si potesse aggirare scoprendo per caso la passphrase giusta durante la finestra non
sarebbe un lockout vero."""
import hmac
import time

from core.auth_provider import AuthProvider, PasskeyProvider, WindowsHelloProvider


class AuthGate:
    # F1.4.3: soglia e durata del lockout dopo tentativi falliti consecutivi. Valori di
    # compromesso (non da un'analisi formale, vedi il docstring del modulo), esposti come
    # attributi di classe e non hardcoded dentro check() cosi' i test possono sovrascriverli
    # su una singola istanza per restare veloci, come gia' fatto altrove nel progetto per
    # timeout configurabili (es. ReminderScheduler._stop_timeout_seconds).
    MAX_CONSECUTIVE_FAILURES = 5
    LOCKOUT_SECONDS = 60.0

    def __init__(
        self, passphrase: str | None = None, windows_hello_enabled: bool = False, windows_hello_verify=None,
        passkey_provider: AuthProvider | None = None,
    ):
        self.passphrase = passphrase.strip() if passphrase else None
        self.windows_hello_enabled = bool(windows_hello_enabled)
        # Iniettabile per i test (vedi core/windows_hello.py sul perche' verify() non deve MAI
        # essere lasciata chiamare l'API vera in un test automatico): di default None, risolto
        # pigramente alla prima chiamata reale (tramite WindowsHelloProvider, F1.4.4 - vedi
        # core/auth_provider.py) cosi' importare questo modulo non importa mai winsdk quando
        # Windows Hello e' disattivato (il caso comune).
        self._windows_hello_verify = windows_hello_verify
        # F1.4.4: fattore OPZIONALE per operazioni cross-device ad alto impatto (companion/
        # mobile) - default a PasskeyProvider(), onestamente inerte finche' non esiste un vero
        # registro di passkey (vedi core/auth_provider.py). Iniettabile per gli stessi motivi di
        # windows_hello_verify sopra: un test/futuro provider reale non deve mai passare da qui.
        self.passkey_provider = passkey_provider or PasskeyProvider()
        self._consecutive_failures = 0
        self._locked_until = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.passphrase) or self.windows_hello_enabled

    def is_locked_out(self) -> bool:
        """Vero se un lockout e' attualmente in corso (indipendentemente da quale passphrase
        verrebbe tentata)."""
        return time.monotonic() < self._locked_until

    def lockout_remaining_seconds(self) -> float:
        """Quanti secondi mancano alla fine del lockout corrente, 0.0 se non ce n'e' uno attivo."""
        return max(0.0, self._locked_until - time.monotonic())

    def check(self, attempt: str) -> bool:
        """Vero se 'attempt' corrisponde alla passphrase configurata. Sempre falso se non e'
        stata configurata: non c'e' nulla con cui corrispondere. hmac.compare_digest invece di
        '==' (vedi il docstring del modulo): un confronto a tempo costante rispetto al contenuto,
        non solo alla lunghezza dei due lati.

        F1.4.3: durante un lockout attivo ritorna sempre False SENZA confrontare affatto -
        neanche una passphrase corretta viene accettata finche' il lockout non scade (vedi il
        docstring del modulo sul perche'). Un successo azzera il contatore dei fallimenti; un
        fallimento lo incrementa e, raggiunta la soglia, apre un nuovo lockout."""
        if not self.passphrase:
            return False
        if self.is_locked_out():
            return False
        matched = hmac.compare_digest((attempt or "").strip(), self.passphrase)
        if matched:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                self._locked_until = time.monotonic() + self.LOCKOUT_SECONDS
                self._consecutive_failures = 0
        return matched

    def verify_with_windows_hello(self, reason: str) -> bool:
        """Mostra il prompt nativo di Windows Hello (se attivo) e restituisce se ha verificato
        l'utente per davvero. False immediato, senza nessuna chiamata, se windows_hello_enabled
        e' spento - non prova nemmeno a controllare se e' disponibile su questa macchina."""
        if not self.windows_hello_enabled:
            return False
        if self._windows_hello_verify is None:
            self._windows_hello_verify = WindowsHelloProvider().verify
        return self._windows_hello_verify(reason)

    def verify_with_passkey(self, reason: str) -> bool:
        """F1.4.4: stesso schema di verify_with_windows_hello sopra, ma per il fattore OPZIONALE
        "companion/mobile e operazioni cross-device ad alto impatto" - tramite l'adapter
        AuthProvider (core/auth_provider.py), mai una chiamata diretta a un SDK specifico da
        questa classe. False se il provider dichiara di non essere disponibile: oggi sempre,
        finche' PasskeyProvider resta onestamente inerte (vedi il suo docstring)."""
        if not self.passkey_provider.is_available():
            return False
        return self.passkey_provider.verify(reason)
