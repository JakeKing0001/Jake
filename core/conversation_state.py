import threading
from collections import deque
from copy import deepcopy


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
    doppia conferma. Non risolve l'intero problema di "ownership della sessione" (due canali che
    hanno bisogno CIASCUNO di una propria conferma nello stesso istante si sovrascrivono ancora a
    vicenda - servirebbe un'identita' di canale/sessione vera, non ancora modellata): chiude la
    forma piu' grave e concreta del buco (doppia esecuzione della STESSA azione), non l'intera
    fase F1.8.1."""

    # Chiavi dei risultati che valgono come "riferimenti recenti" per i pronomi (v3.1):
    # "chiudilo", "aprilo", "leggilo" prendono il valore da qui.
    ENTITY_KEYS = ("app", "path", "url", "title", "query", "name", "text", "contact")

    def __init__(self, short_term_limit: int = 10):
        self._pending_action: dict | None = None
        self._pending_action_lock = threading.Lock()
        self._short_term_history: deque[dict] = deque(maxlen=short_term_limit)
        self._last_search_results: list[dict] = []
        self._entities: dict = {}

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
        """Restituisce l'azione in attesa, se presente, SENZA consumarla - usato solo da
        controlli in sola lettura (es. core/voice/wake_word_session.py, per decidere se
        rilassare il requisito della wake word). Chi deve poi AGIRE su un'azione in sospeso
        (JakeCore._process) deve usare take_pending_action(), non questo + clear_pending_action()
        separati (vedi F1.8.1 nel docstring della classe)."""
        with self._pending_action_lock:
            return deepcopy(self._pending_action)

    def has_pending_action(self) -> bool:
        """Indica se esiste un'azione in attesa di conferma (sola lettura, stesso avvertimento
        di get_pending_action())."""
        with self._pending_action_lock:
            return self._pending_action is not None

    def set_pending_action(self, action: dict):
        """Salva una nuova azione in attesa di conferma."""
        with self._pending_action_lock:
            self._pending_action = deepcopy(action)

    def clear_pending_action(self):
        """Cancella l'azione in attesa."""
        with self._pending_action_lock:
            self._pending_action = None

    def take_pending_action(self):
        """F1.8.1: legge E cancella l'azione in attesa in UN'UNICA operazione atomica, invece di
        has_pending_action()/get_pending_action()/clear_pending_action() come tre chiamate
        separate (la causa esatta della doppia esecuzione riprodotta nel docstring della classe).
        Restituisce None se non c'era nulla in sospeso. Al massimo UN chiamante concorrente puo'
        mai ricevere una data azione (gli altri ricevono None): chi la riceve e' l'unico
        autorizzato a interpretarla come una conferma."""
        with self._pending_action_lock:
            action = self._pending_action
            self._pending_action = None
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
