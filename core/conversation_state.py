from collections import deque
from copy import deepcopy


class ConversationStateManager:
    """Gestisce lo stato conversazionale temporaneo di Jake (memoria a breve termine, solo in RAM)."""

    # Chiavi dei risultati che valgono come "riferimenti recenti" per i pronomi (v3.1):
    # "chiudilo", "aprilo", "leggilo" prendono il valore da qui.
    ENTITY_KEYS = ("app", "path", "url", "title", "query", "name", "text", "contact")

    def __init__(self, short_term_limit: int = 10):
        self._pending_action: dict | None = None
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
        """Restituisce l'azione in attesa, se presente."""
        return deepcopy(self._pending_action)

    def has_pending_action(self) -> bool:
        """Indica se esiste un'azione in attesa di conferma."""
        return self._pending_action is not None

    def set_pending_action(self, action: dict):
        """Salva una nuova azione in attesa di conferma."""
        self._pending_action = deepcopy(action)

    def clear_pending_action(self):
        """Cancella l'azione in attesa."""
        self._pending_action = None

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
