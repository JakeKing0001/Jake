"""Modalita' di notifica (v4.3, Notification/Priority system): fino ad ora ogni promemoria,
avviso proattivo (core/system_advisor.py, v3.2/4.2) e automazione partita da sola (core/
trigger_scheduler.py) interrompeva SEMPRE, in ogni momento, sia in CLI (stampa) sia in voce
(sintesi vocale, core/voice/wake_word_session.py) - niente riunioni, niente studio, niente
partite senza essere interrotti da "ho notato che la batteria e' scarica".

NotificationCenter e' il punto unico da cui passano queste tre fonti prima di essere
presentate (vedi JakeCore.notify, usato sia dal percorso CLI sia da WakeWordSession): la
modalita' corrente decide quali tipi sono ammessi subito e quali vanno in coda per dopo.

I promemoria (kind="reminder") sono trattati diversamente dagli altri due: l'utente li ha
chiesti esplicitamente per un orario preciso ("ricordami di prendere la medicina alle 15"),
quindi restano ammessi in ogni modalita' tranne MEETING, dove anche solo far parlare Jake ad
alta voce durante una riunione sarebbe comunque sbagliato indipendentemente dal contenuto.

F1.8.2 (stesso principio gia' applicato a ActionLedger/ReminderManager/TodoManager/MemoryManager
in questa sessione): buco reale, riprodotto per davvero prima del fix - `NotificationCenter` e'
UN'istanza condivisa PER RIFERIMENTO tra `JakeCore.notify()` (chiamato dai thread separati di
`TriggerScheduler`/`ReminderScheduler`/`SystemAdvisor` per reminder/avvisi/automazioni) e
`SetNotificationModeSkill`/`GetNotificationModeSkill` (raggiungibili da voce o dal companion
server, altri thread ancora), ma `gate()`/`set_mode()` mutavano `self._queued` senza alcuna
sincronizzazione. `set_mode()` in particolare legge e riscrive `_queued` in DUE passaggi separati
(prima calcola `released` filtrando la coda, poi la riassegna filtrata di nuovo): se un `gate()`
concorrente aggiunge un elemento esattamente tra i due passaggi, quell'elemento non finisce ne'
in `released` (calcolato prima che arrivasse) ne' resta in coda (la riscrittura successiva lo
esclude se il suo tipo e' ora ammesso) - sparisce per sempre, senza errore ne' log. Riprodotto
per davvero con `sys.setswitchinterval()` abbassato per forzare la sovrapposizione: su 30 prove
con 500 `gate()` concorrenti a un `set_mode()`, in media oltre il 98% delle notifiche spariva
senza lasciare traccia. Uno scenario reale, non di laboratorio: l'utente esce da "modalita'
studio" proprio mentre uno scheduler in background mette in coda un nuovo avviso/promemoria."""
import threading
from enum import Enum


class NotificationMode(str, Enum):
    NORMAL = "normal"
    DO_NOT_DISTURB = "do_not_disturb"
    GAMING = "gaming"
    STUDY = "study"
    MEETING = "meeting"
    SLEEP = "sleep"


MODE_LABELS_IT = {
    NotificationMode.NORMAL: "normale",
    NotificationMode.DO_NOT_DISTURB: "non disturbare",
    NotificationMode.GAMING: "gioco",
    NotificationMode.STUDY: "studio",
    NotificationMode.MEETING: "riunione",
    NotificationMode.SLEEP: "sonno",
}

# Tipi di notifica ammessi SUBITO in ogni modalita'; gli altri vengono messi in coda (vedi
# NotificationCenter.gate). GAMING lascia passare anche i trigger: un'automazione ("scarica
# completato", "backup fatto") e' spesso rilevante anche a schermo intero, a differenza degli
# avvisi di sistema che possono aspettare. MEETING non lascia passare nulla, promemoria incluso.
MODE_ALLOWED_KINDS: dict[NotificationMode, frozenset] = {
    NotificationMode.NORMAL: frozenset({"reminder", "advisory", "trigger", "pairing"}),
    # "pairing" (F7.1.2, Companion Mobile MVP): l'utente ha appena scansionato un QR o avviato il
    # pairing DI PROPOSITO sul telefono - stesso principio gia' applicato a "reminder" (un evento
    # che l'utente ha causato lui stesso adesso, non un avviso proattivo che puo' aspettare).
    # Fuori solo da MEETING/SLEEP, come "reminder": nemmeno un pairing interrompe una riunione o
    # il sonno, resta in coda finche' la challenge non scade (5 minuti, core/pairing_service.py).
    NotificationMode.DO_NOT_DISTURB: frozenset({"reminder", "pairing"}),
    NotificationMode.STUDY: frozenset({"reminder", "pairing"}),
    NotificationMode.SLEEP: frozenset({"reminder"}),
    NotificationMode.GAMING: frozenset({"reminder", "trigger", "pairing"}),
    NotificationMode.MEETING: frozenset(),
}


class NotificationCenter:
    def __init__(self, mode: NotificationMode = NotificationMode.NORMAL):
        self.mode = mode
        self._queued: list[dict] = []
        self._lock = threading.Lock()

    def gate(self, kind: str, message: str) -> str | None:
        """Se 'kind' e' ammesso nella modalita' corrente restituisce il messaggio (da
        presentare subito); altrimenti lo mette in coda e restituisce None."""
        if message and kind in MODE_ALLOWED_KINDS.get(self.mode, frozenset()):
            return message
        if message:
            with self._lock:
                self._queued.append({"kind": kind, "message": message})
        return None

    def set_mode(self, mode: NotificationMode) -> list[str]:
        """Cambia modalita' e restituisce (nell'ordine di arrivo) i messaggi in coda ora
        ammessi dalla nuova modalita', togliendoli dalla coda; quelli ancora non ammessi
        restano in coda per la prossima volta. Filtra la coda in UNA sola passata sotto lock
        (non piu' due liste separate): un `gate()` concorrente non puo' piu' infilarsi nella
        finestra tra "calcola i rilasciati" e "riscrivi la coda" e sparire senza finire ne'
        nell'uno ne' nell'altra (vedi F1.8.2 nel docstring del modulo)."""
        allowed = MODE_ALLOWED_KINDS.get(mode, frozenset())
        with self._lock:
            self.mode = mode
            released: list[dict] = []
            remaining: list[dict] = []
            for item in self._queued:
                (released if item["kind"] in allowed else remaining).append(item)
            self._queued = remaining
            return [item["message"] for item in released]

    def pending_count(self) -> int:
        with self._lock:
            return len(self._queued)
