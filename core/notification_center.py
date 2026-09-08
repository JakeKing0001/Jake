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
alta voce durante una riunione sarebbe comunque sbagliato indipendentemente dal contenuto."""
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
    NotificationMode.NORMAL: frozenset({"reminder", "advisory", "trigger"}),
    NotificationMode.DO_NOT_DISTURB: frozenset({"reminder"}),
    NotificationMode.STUDY: frozenset({"reminder"}),
    NotificationMode.SLEEP: frozenset({"reminder"}),
    NotificationMode.GAMING: frozenset({"reminder", "trigger"}),
    NotificationMode.MEETING: frozenset(),
}


class NotificationCenter:
    def __init__(self, mode: NotificationMode = NotificationMode.NORMAL):
        self.mode = mode
        self._queued: list[dict] = []

    def gate(self, kind: str, message: str) -> str | None:
        """Se 'kind' e' ammesso nella modalita' corrente restituisce il messaggio (da
        presentare subito); altrimenti lo mette in coda e restituisce None."""
        if message and kind in MODE_ALLOWED_KINDS.get(self.mode, frozenset()):
            return message
        if message:
            self._queued.append({"kind": kind, "message": message})
        return None

    def set_mode(self, mode: NotificationMode) -> list[str]:
        """Cambia modalita' e restituisce (nell'ordine di arrivo) i messaggi in coda ora
        ammessi dalla nuova modalita', togliendoli dalla coda; quelli ancora non ammessi
        restano in coda per la prossima volta."""
        self.mode = mode
        allowed = MODE_ALLOWED_KINDS.get(mode, frozenset())
        released = [item["message"] for item in self._queued if item["kind"] in allowed]
        self._queued = [item for item in self._queued if item["kind"] not in allowed]
        return released

    def pending_count(self) -> int:
        return len(self._queued)
