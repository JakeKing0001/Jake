import threading
from collections import deque
from copy import deepcopy

from core.request_context import current_device_id


class ConversationStateManager:
    """Gestisce lo stato conversazionale temporaneo di Jake (memoria a breve termine, solo in RAM).

    F1.8.1 ("definire ownership della sessione... per azioni concorrenti"): buco reale, riprodotto
    per davvero prima del fix - `JakeCore.answer()` e' l'unico punto d'ingresso condiviso sia dal
    loop voce (thread principale) sia da `core/companion_server.py` (un `ThreadingHTTPServer`: OGNI
    richiesta HTTP gira sul PROPRIO thread), quindi due turni possono arrivare DAVVERO in
    concorrenza sulla STESSA istanza di `JakeCore`. Prima di questo fix, il controllo di un'azione
    in sospeso era tre chiamate SEPARATE (`has_pending_action()`, `get_pending_action()`,
    `clear_pending_action()`) senza alcuna sincronizzazione tra loro: due thread potevano
    osservare ENTRAMBI la stessa azione DESTRUCTIVE/ADMIN ancora in sospeso prima che uno dei due
    la ripulisse, ed eseguirla DUE VOLTE (una per canale) - riprodotto con due thread reali in
    corsa sulla stessa azione. `take_pending_action()` (sotto) rende get+clear un'unica
    operazione atomica: al massimo UN chiamante puo' mai "vincere" una data azione in sospeso, gli
    altri la vedono gia' consumata (`None`) e procedono come un comando nuovo, mai come una
    doppia conferma.

    F1.8.1 (chiusura, uno slot per canale) — 13/09/2026: prima di questa correzione esisteva UN
    solo slot globale (`_pending_action`), quindi due dispositivi companion CIASCUNO con una
    propria richiesta di conferma nello stesso istante si sovrascrivevano a vicenda - non una
    doppia esecuzione (gia' chiusa sopra), ma una PERDITA: il secondo `set_pending_action()`
    cancellava silenziosamente la richiesta del primo dispositivo, che a quel punto non poteva
    piu' confermarla (un "si'" successivo avrebbe confermato l'azione SBAGLIATA, quella del
    secondo dispositivo). Corretto sostituendo lo slot singolo con un dizionario `{canale: azione}`
    (`_pending_actions`), dove il canale e' `core.request_context.current_device_id()` - lo stesso
    identificatore per-thread gia' introdotto per il ledger (F1.2.3/F1.8.1, fondamenta): `None`
    (la voce locale, o un client companion che non manda `device_id`) e ogni device_id noto hanno
    ora ciascuno il proprio slot indipendente, senza bisogno di passare un parametro esplicito a
    ogni metodo (stesso principio gia' usato per `ActionReceipt.device_id`). Un solo `Lock` guarda
    l'intero dizionario (non un lock per chiave): la contesa e' irrilevante qui, un'operazione su
    una conferma in sospeso e' rara e leggera, mentre un lock per chiave aggiungerebbe complessita'
    senza un beneficio misurabile. `take_pending_action()`/`clear_pending_action()` rimuovono la
    chiave con `pop()` invece di lasciarla con valore `None`: un dizionario che crescesse con una
    voce per ogni device_id mai visto, anche dopo che la sua conferma e' stata consumata, sarebbe
    una perdita di memoria lenta ma reale su un processo di lunga durata con molti dispositivi
    companion diversi nel tempo."""

    # Chiavi dei risultati che valgono come "riferimenti recenti" per i pronomi (v3.1):
    # "chiudilo", "aprilo", "leggilo" prendono il valore da qui.
    ENTITY_KEYS = ("app", "path", "url", "title", "query", "name", "text", "contact")

    def __init__(self, short_term_limit: int = 10):
        # F1.8.1: una voce per canale (vedi il docstring della classe), non uno slot singolo -
        # la chiave e' current_device_id() (None per la voce locale), letta internamente da ogni
        # metodo sotto invece di essere un parametro esplicito.
        self._pending_actions: dict[str | None, dict] = {}
        self._pending_action_lock = threading.Lock()
        # F4.4.1/F4.5.3: chiamata (azione o None) FUORI dal lock ogni volta che l'azione in sospeso
        # di un canale cambia davvero - JakeCore la usa per l'evento CONFIRMATION dell'HUD.
        self.on_pending_change = None
        self._short_term_history: deque[dict] = deque(maxlen=short_term_limit)
        self._last_search_results: list[dict] = []
        self._entities: dict = {}

    def swap_state(self, other: "ConversationStateManager") -> None:
        """Scambia atomicamente lo stato in RAM con un altro namespace conversazionale.

        F2.7 usa questo metodo per far vedere ai molti collaboratori che conservano un
        riferimento alla stessa istanza (skill, router e JakeCore) il profilo del singolo turno,
        senza riassegnare l'oggetto. Il chiamante serializza l'intero turno; l'ordinamento dei
        lock rende comunque sicuro lo scambio anche se due thread tentano l'operazione insieme.
        """
        if other is self:
            return
        first, second = sorted((self, other), key=id)
        with first._pending_action_lock:
            with second._pending_action_lock:
                self._pending_actions, other._pending_actions = other._pending_actions, self._pending_actions
                self._short_term_history, other._short_term_history = (
                    other._short_term_history,
                    self._short_term_history,
                )
                self._last_search_results, other._last_search_results = (
                    other._last_search_results,
                    self._last_search_results,
                )
                self._entities, other._entities = other._entities, self._entities

    def add_turn(self, role: str, text: str) -> None:
        """Aggiunge un turno al buffer di conversazione a breve termine."""
        self._short_term_history.append({"role": role, "text": text})

    def get_short_term_history(self) -> list[dict]:
        """Restituisce gli ultimi turni tenuti in RAM per questa sessione."""
        return list(self._short_term_history)

    def set_last_search_results(self, results: list[dict]) -> None:
        """Salva i risultati dell'ultima ricerca file, per riferimento con 'apri il primo risultato'."""
        self._last_search_results = deepcopy(results)

    def get_last_search_results(self) -> list[dict]:
        """Restituisce i risultati dell'ultima ricerca file di questa sessione."""
        return deepcopy(self._last_search_results)

    def get_pending_action(self):
        """Restituisce l'azione in attesa PER QUESTO CANALE (current_device_id()), se presente,
        SENZA consumarla - usato solo da controlli in sola lettura (es. core/voice/
        wake_word_session.py, per decidere se rilassare il requisito della wake word). Chi deve
        poi AGIRE su un'azione in sospeso (JakeCore._process) deve usare take_pending_action(),
        non questo + clear_pending_action() separati (vedi F1.8.1 nel docstring della classe)."""
        with self._pending_action_lock:
            action = self._pending_actions.get(current_device_id())
            return deepcopy(action) if action is not None else None

    def has_pending_action(self) -> bool:
        """Indica se esiste un'azione in attesa di conferma PER QUESTO CANALE (sola lettura,
        stesso avvertimento di get_pending_action())."""
        with self._pending_action_lock:
            return current_device_id() in self._pending_actions

    def set_pending_action(self, action: dict):
        """Salva una nuova azione in attesa di conferma PER QUESTO CANALE (current_device_id()),
        senza toccare quella di nessun altro canale."""
        with self._pending_action_lock:
            self._pending_actions[current_device_id()] = deepcopy(action)
        self._notify_pending(action)

    def clear_pending_action(self):
        """Cancella l'azione in attesa PER QUESTO CANALE."""
        with self._pending_action_lock:
            removed = self._pending_actions.pop(current_device_id(), None)
        if removed is not None:
            self._notify_pending(None)

    def _notify_pending(self, action) -> None:
        callback = self.on_pending_change
        if callback is None:
            return
        try:
            callback(deepcopy(action) if action is not None else None)
        except Exception:
            pass  # un osservatore guasto non deve mai rompere la conferma

    def take_pending_action(self):
        """F1.8.1: legge E cancella l'azione in attesa PER QUESTO CANALE in UN'UNICA operazione
        atomica, invece di has_pending_action()/get_pending_action()/clear_pending_action() come
        tre chiamate separate (la causa esatta della doppia esecuzione riprodotta nel docstring
        della classe). Restituisce None se non c'era nulla in sospeso PER QUESTO CANALE.
        Al massimo UN chiamante concorrente sullo STESSO canale puo' mai ricevere una data azione
        (gli altri ricevono None): chi la riceve e' l'unico autorizzato a interpretarla come una
        conferma. Un canale diverso (un altro device_id, o la voce locale) ha il proprio slot
        indipendente: non vede ne' interferisce con questa azione."""
        with self._pending_action_lock:
            action = self._pending_actions.pop(current_device_id(), None)
        if action is not None:
            self._notify_pending(None)
        return deepcopy(action) if action is not None else None

    # ---- riferimenti recenti (v3.1) -------------------------------------------------------

    def remember_entities(self, intent: str, parameters: dict, data: dict) -> None:
        """Aggiorna i riferimenti recenti da un comando riuscito."""
        merged = dict(parameters or {})
        for key, value in (data or {}).items():
            if key in self.ENTITY_KEYS and value:
                merged[key] = value
        if intent in ("FIND_FILE", "SEARCH_FILES", "SEMANTIC_SEARCH_FILES"):
            results = (data or {}).get("results") or []
            first = results[0] if results else None
            if isinstance(first, dict):
                first = first.get("path")
            if first:
                merged["path"] = first
        for key in self.ENTITY_KEYS:
            value = merged.get(key)
            if isinstance(value, str) and value.strip() and len(value) <= 200:
                self._entities[key] = value.strip()
        if intent in ("OPEN_APP", "FOCUS_WINDOW", "CLOSE_APP", "CLOSE_WINDOW"):
            app = merged.get("app") or merged.get("title") or merged.get("name")
            if app:
                self._entities["app"] = str(app)

    def get_entities(self) -> dict:
        return dict(self._entities)

    def entities_summary(self) -> str:
        labels = {"app": "app", "path": "file", "url": "sito", "title": "finestra", "query": "ricerca", "name": "nome", "contact": "contatto"}
        parts = [f"{labels[key]}: {value}" for key, value in self._entities.items() if key in labels]
        return "; ".join(parts)
