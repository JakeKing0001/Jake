import threading
from collections import deque
from datetime import datetime


class DesktopContextTracker:
    """Tiene traccia, in background e a basso costo, delle finestre/app usate di recente
    (v2.0: contestualizzazione "leggera" del desktop). Solo il titolo della finestra attiva,
    controllato a intervalli: nessuna lettura continua dello schermo/OCR (troppo costosa
    e invasiva per girare sempre)."""

    def __init__(self, poll_seconds: float = 3.0, history_size: int = 10):
        self.poll_seconds = poll_seconds
        self._history = deque(maxlen=history_size)
        self._current_title = None
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        from core.vision.screen import get_active_window_title

        while not self._stop_event.is_set():
            try:
                title = get_active_window_title()
            except Exception:
                title = None

            if title and title != self._current_title:
                with self._lock:
                    self._current_title = title
                    self._history.append({"title": title, "at": datetime.now()})

            self._stop_event.wait(self.poll_seconds)

    def get_current_window(self) -> str | None:
        with self._lock:
            return self._current_title

    def get_recent_windows(self, limit: int = 5) -> list[str]:
        with self._lock:
            titles = [entry["title"] for entry in reversed(self._history)]

        seen = set()
        unique = []
        for title in titles:
            if title not in seen:
                seen.add(title)
                unique.append(title)
            if len(unique) >= limit:
                break
        return unique

    def context_summary(self) -> str:
        """Riga di contesto da iniettare nei prompt di sistema di Ollama/Planner."""
        recent = self.get_recent_windows()
        if not recent:
            return ""
        return "Finestre/app usate di recente sul desktop: " + "; ".join(recent)
