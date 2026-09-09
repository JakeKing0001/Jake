"""Budget di autonomia per le automazioni che partono da sole (F6, Proactive Intelligence &
Autonomy - "Autonomy budget per tempo, numero azioni... stop automatico al limite" in
ROADMAP.md).

Applicato solo ai trigger (core/trigger_scheduler.py: un'automazione salvata che parte senza che
l'utente abbia chiesto nulla in quel momento), non ai passi dell'agente durante una conversazione
in corso - li' l'utente ha gia' chiesto lui il compito composto, e un limite al numero di passi
esiste gia' altrove (TaskAgent.MAX_STEPS/RUN_TIMEOUT_SECONDS, vedi core/agent.py) - ne' a un
comando diretto. Un trigger che continua a far partire automazioni (una condizione mal scritta
che scatta ogni minuto, un bug che lo fa ripetere) e' esattamente il caso che un budget deve
fermare: "autonomia senza verifica/permessi/memoria moltiplica gli errori", vedi l'ordine di
costruzione in ROADMAP.md.

Una finestra scorrevole (non un contatore che si azzera a un orario fisso): "20 automazioni
nell'ultima ora", non "20 automazioni da mezzanotte" - cosi' un trigger che scatta ripetutamente
viene fermato entro l'ora, non solo il giorno dopo."""
import threading
import time


class AutonomyBudget:
    """time_source e' iniettabile (default time.time) per test deterministici, senza dover
    aspettare per davvero che una finestra scorra."""

    def __init__(self, max_actions: int = 20, window_seconds: float = 3600, time_source=time.time):
        self.max_actions = max_actions
        self.window_seconds = window_seconds
        self._time_source = time_source
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.pop(0)

    def is_exceeded(self) -> bool:
        """Vero se il budget e' gia' esaurito - chi chiama deve controllarlo PRIMA di far
        partire un'altra automazione, non dopo (vedi core/trigger_scheduler.py)."""
        with self._lock:
            self._prune(self._time_source())
            return len(self._timestamps) >= self.max_actions

    def record(self) -> None:
        """Registra un'automazione appena partita. Chiamare solo DOPO aver verificato
        is_exceeded() == False, altrimenti il budget diventa un contatore senza effetto."""
        with self._lock:
            now = self._time_source()
            self._prune(now)
            self._timestamps.append(now)

    def remaining(self) -> int:
        with self._lock:
            self._prune(self._time_source())
            return max(0, self.max_actions - len(self._timestamps))

    def reset(self) -> None:
        """Svuota la finestra: usato dal kill switch (RESET_KILL_SWITCH) per non lasciare un
        budget esaurito quando l'utente ha appena detto esplicitamente 'riprendi'."""
        with self._lock:
            self._timestamps.clear()
