from abc import ABC, abstractmethod

from core.command import Command


class IntentProvider(ABC):
    """Interfaccia per i componenti che trasformano testo in Command."""

    @abstractmethod
    def detect_intent(self, text: str) -> Command:
        """Riconosce l'intent e restituisce il comando corrispondente."""
        raise NotImplementedError
