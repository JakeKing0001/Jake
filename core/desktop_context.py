import threading
import time
from collections import deque
from datetime import datetime

from core.logger import get_logger

# Anteprima appunti troncata (v3.5, World Context Engine): abbastanza per far capire al modello
# "di cosa sta parlando" un "riassumi questo"/"traduci questo" detto senza specificare cosa,
# ma non tanto da gonfiare ogni prompt con un blocco di testo intero copiato per altri motivi.
CLIPBOARD_PREVIEW_MAX_CHARS = 120

# F5.6.4 (privacy): formati con cui un'app chiede a Windows di non far leggere cio' che ha copiato ai programmi che
# sorvegliano gli appunti (password manager, campi password). Se presenti, Jake non ne legge nemmeno l'anteprima.
CLIPBOARD_EXCLUDE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing",)
CLIPBOARD_HISTORY_FORMAT = "CanIncludeInClipboardHistory"  # DWORD 0 = "non tenerlo nella cronologia"


def clipboard_is_private(win32clipboard) -> bool:
    """Vero se gli appunti aperti portano un segnale di contenuto da non monitorare."""
    for name in CLIPBOARD_EXCLUDE_FORMATS:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.RegisterClipboardFormat(name)):
            return True
    history = win32clipboard.RegisterClipboardFormat(CLIPBOARD_HISTORY_FORMAT)
    if win32clipboard.IsClipboardFormatAvailable(history):
        value = win32clipboard.GetClipboardData(history)
        return bool(value) and int.from_bytes(bytes(value)[:4], "little") == 0
    return False


class DesktopContextTracker:
    """Tiene traccia, in background e a basso costo, di cosa sta facendo l'utente sul desktop
    in questo momento (v2.0 finestra attiva; v3.5 World Context Engine: anche le finestre
    aperte e un'anteprima degli appunti). Letture leggere a intervalli, mai continue (niente
    OCR/screenshot qui: troppo costoso e invasivo per girare sempre in background)."""

    def __init__(self, poll_seconds: float = 3.0, history_size: int = 10, stop_timeout_seconds: float = 2.0,
                 clock=time.monotonic):
        self.poll_seconds = poll_seconds
        # F5.6.7: un segnale non riletto da troppo tempo (lettura che fallisce, thread fermo) non e' piu' "adesso"
        self.stale_after_seconds = max(10.0, 3 * poll_seconds)
        self._clock = clock
        self._fresh_at: dict[str, float] = {}
        self._history: deque[dict] = deque(maxlen=history_size)
        self._current_title: str | None = None
        self._open_windows: list[str] = []
        self._clipboard_preview: str | None = None
        self._clipboard_private = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._logger = get_logger()
        # F1.8.5: configurabile solo per i test - il comportamento di produzione resta invariato.
        self._stop_timeout_seconds = stop_timeout_seconds

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # F1.8.5 ("aggiungere deadlock timeout e diagnosi"): vedi core/scheduler.py::
        # ReminderScheduler.stop() per il buco reale riprodotto e il ragionamento completo -
        # stesso schema qui.
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._stop_timeout_seconds)
            if self._thread.is_alive():
                self._logger.warning(
                    "DesktopContextTracker non si e' fermato entro il timeout: il thread "
                    "precedente e' ancora in esecuzione (probabilmente bloccato in una lettura "
                    "lenta di finestra/appunti)."
                )

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
        with self._lock:
            self._fresh_at["window"] = self._clock()
            if title and title != self._current_title:
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
            self._fresh_at["open_windows"] = self._clock()

    def _poll_clipboard(self) -> None:
        try:
            import win32clipboard

            win32clipboard.OpenClipboard()
            try:
                private = clipboard_is_private(win32clipboard)
                text = None if private else win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            private, text = False, None

        preview = None
        if text and text.strip():
            stripped = text.strip().replace("\n", " ")
            preview = stripped[:CLIPBOARD_PREVIEW_MAX_CHARS]
            if len(stripped) > CLIPBOARD_PREVIEW_MAX_CHARS:
                preview += "…"
        with self._lock:
            self._clipboard_preview = preview
            self._clipboard_private = private
            self._fresh_at["clipboard"] = self._clock()

    def _fresh(self, signal: str) -> bool:
        with self._lock:
            at = self._fresh_at.get(signal)
        return at is not None and self._clock() - at <= self.stale_after_seconds

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
        # F5.6.7: "ora" solo se riletto di recente; un valore vecchio non si presenta come stato attuale
        open_windows = self.get_open_windows() if self._fresh("open_windows") else []
        if open_windows:
            parts.append("Finestre aperte ora: " + "; ".join(open_windows[:8]))
        clipboard = self.get_clipboard_preview() if self._fresh("clipboard") else None
        if clipboard:
            parts.append(f'Appunti: "{clipboard}"')
        elif self._clipboard_private and self._fresh("clipboard"):
            parts.append("Appunti: contenuto privato (non letto)")
        return " | ".join(parts)
