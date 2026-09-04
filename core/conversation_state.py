from collections import deque
from copy import deepcopy


class ConversationStateManager:
    """Gestisce lo stato conversazionale temporaneo di Jake (memoria a breve termine, solo in RAM)."""

    def __init__(self, short_term_limit: int = 10):
        self._pending_action = None
        self._short_term_history = deque(maxlen=short_term_limit)
        self._last_search_results = []

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