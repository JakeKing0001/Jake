import threading
from collections import deque
from datetime import datetime

# Anteprima appunti troncata (v3.5, World Context Engine): abbastanza per far capire al modello
# "di cosa sta parlando" un "riassumi questo"/"traduci questo" detto senza specificare cosa,
# ma non tanto da gonfiare ogni prompt con un blocco di testo intero copiato per altri motivi.
CLIPBOARD_PREVIEW_MAX_CHARS = 120


class DesktopContextTracker:
    """Tiene traccia, in background e a basso costo, di cosa sta facendo l'utente sul desktop
    in questo momento (v2.0 finestra attiva; v3.5 World Context Engine: anche le finestre
    aperte e un'anteprima degli appunti). Letture leggere a intervalli, mai continue (niente
    OCR/screenshot qui: troppo costoso e invasivo per girare sempre in background)."""

    def __init__(self, poll_seconds: float = 3.0, history_size: int = 10):
        self.poll_seconds = poll_seconds
        self._history = deque(maxlen=history_size)
        self._current_title = None
        self._open_windows: list[str] = []
        self._clipboard_preview: str | None = None
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
        while not self._stop_event.is_set():
            self._poll_active_window()
            self._poll_open_windows()
            self._poll_clipboard()
            self._stop_event.wait(self.poll_seconds)

    def _poll_active_window(self) -> None:
        from core.vision.screen import get_active_window_title

        try:
            title = get_active_window_title()
        except Exception:
            return
        if title and title != self._current_title:
            with self._lock:
                self._current_title = title
                self._history.append({"title": title, "at": datetime.now()})

    def _poll_open_windows(self) -> None:
        from core.vision.screen import list_open_window_titles

        try:
            titles = list_open_window_titles()
        except Exception:
            return
        with self._lock:
            self._open_windows = titles

    def _poll_clipboard(self) -> None:
        try:
            import win32clipboard

            win32clipboard.OpenClipboard()
            try:
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            text = None

        preview = None
        if text and text.strip():
            stripped = text.strip().replace("\n", " ")
            preview = stripped[:CLIPBOARD_PREVIEW_MAX_CHARS]
            if len(stripped) > CLIPBOARD_PREVIEW_MAX_CHARS:
                preview += "…"
        with self._lock:
            self._clipboard_preview = preview

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

    def get_open_windows(self) -> list[str]:
        with self._lock:
            return list(self._open_windows)

    def get_clipboard_preview(self) -> str | None:
        with self._lock:
            return self._clipboard_preview

    def context_summary(self) -> str:
        """Riga di contesto da iniettare nei prompt di sistema di Ollama/Planner/Agente."""
        parts = []
        recent = self.get_recent_windows()
        if recent:
            parts.append("Finestre/app usate di recente sul desktop: " + "; ".join(recent))
        open_windows = self.get_open_windows()
        if open_windows:
            parts.append("Finestre aperte ora: " + "; ".join(open_windows[:8]))
        clipboard = self.get_clipboard_preview()
        if clipboard:
            parts.append(f'Appunti: "{clipboard}"')
        return " | ".join(parts)
