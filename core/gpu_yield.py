"""Jake cede la GPU quando serve ad altro e la riprende quando torna libera.

Il budget fisso (`ollama_gpu_budget_mb`) teneva qwen2.5:7b a ~1 GB di VRAM SEMPRE: risposte da ~2,4 s a ~17 s anche
quando la GPU non serviva a nessuno (misura del 04/10/2026). La VRAM serve ad altro solo in momenti precisi: un gioco
o un'app a schermo intero, un programma scelto dall'utente (Blender, un emulatore...), la modalita' "gioco". Solo
allora il modello principale lascia la GPU (scaricato subito, le richieste passano alla CPU con keep_alive breve) e la
voce RVC (torch, ~1 GB) si ferma; finita la situazione il modello torna sulla GPU e viene ricaricato in anticipo.

Segnali, letti ogni POLL_S (nessun contenuto: solo stato di Windows e nomi degli eseguibili):
- SHQueryUserNotificationState: D3D a schermo intero sempre; "busy" (schermo intero, anche borderless) solo se la
  finestra in primo piano non e' di Jake ne' di un browser/lettore video (un video non ha bisogno di quella VRAM);
- `gpu_yield_apps` in settings.json: eseguibili che, se in esecuzione, fanno cedere la GPU (es. "blender.exe");
- la modalita' notifiche "gioco" scelta dall'utente;
- VRAM insufficiente (VramPressure): un altro programma (un addestramento, Blender, un gioco in finestra) ha preso la
  memoria video e il modello non ci sta. Prova del 05/10/2026: con un training da 7,7 GB ogni classificazione andava in
  timeout dopo 25 s (Ollama non riusciva a caricare il modello) invece di passare alla CPU.
Isteresi: si cede dopo ENTER_S di segnale continuo, si riprende dopo EXIT_S senza (un alt-tab non ricarica 4,5 GB).
`gpu_yield_enabled: false` in settings.json disattiva tutto."""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterable

from core.logger import get_logger

POLL_S = 2.0
ENTER_S = 4.0
EXIT_S = 20.0

QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4

# schermo intero che NON giustifica scaricare il modello: Jake stesso e video/browser
DEFAULT_IGNORED_FULLSCREEN = frozenset({
    "jakehud.exe", "python.exe", "pythonw.exe",
    "msedge.exe", "chrome.exe", "firefox.exe", "opera.exe", "opera_gx.exe", "brave.exe", "vivaldi.exe",
    "vlc.exe", "mpc-hc64.exe", "mpv.exe", "potplayermini64.exe", "wmplayer.exe", "microsoft.media.player.exe",
    "applicationframehost.exe", "powerpnt.exe",
})


def _notification_state() -> int | None:
    try:
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return None
        return state.value
    except (AttributeError, OSError):
        return None


def _foreground_exe() -> str | None:
    try:
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return "python.exe"
        handle = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return os.path.basename(buffer.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return None


def running_exes() -> set[str]:
    """Nomi (minuscoli) degli eseguibili in esecuzione, da un'istantanea Toolhelp: nessun processo viene aperto."""
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    names: set[str] = set()
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return names
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            ok = kernel32.Process32FirstW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
            while ok:
                names.add(entry.szExeFile.lower())
                ok = kernel32.Process32NextW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(snapshot))
    except (AttributeError, OSError):
        pass
    return names


def _free_vram_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(out.stdout.split()[0]) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


class VramPressure:
    """Il modello principale non ci sta nella VRAM rimasta libera: "vram:<disponibili><<servono>MB", altrimenti None.

    Disponibile = VRAM libera + quella che il modello sta gia' usando (caricato, la sua memoria e' sua). Il fabbisogno
    e' misurato da Ollama quando il modello e' caricato tutto sulla GPU; prima di una misura e' una stima dal file.
    Letto al massimo ogni `every_s` (nvidia-smi costa qualche decina di ms)."""

    MARGIN_MB = 300

    def __init__(self, model: str, ps: Callable[[], list[dict]], tags: Callable[[], list[dict]],
                 free_vram: Callable[[], int | None] = _free_vram_mb, every_s: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        self.model = model
        self.ps, self.tags, self.free_vram = ps, tags, free_vram
        self.every_s = every_s
        self.clock = clock
        self.needed_mb: int | None = None
        self._at: float | None = None
        self._last: str | None = None

    def _entry(self, models: list[dict]) -> dict | None:
        for entry in models:
            name = str(entry.get("name") or entry.get("model") or "")
            if name in (self.model, f"{self.model}:latest"):
                return entry
        return None

    def _estimate(self) -> int | None:
        entry = self._entry(self.tags())
        size = entry.get("size") if entry else None
        return int(size / 2**20 * 1.15) + 512 if isinstance(size, (int, float)) else None

    def __call__(self) -> str | None:
        now = self.clock()
        if self._at is not None and now - self._at < self.every_s:
            return self._last
        self._at = now
        self._last = None
        try:
            free = self.free_vram()
            if free is None:
                return None
            loaded = self._entry(self.ps())
            own = 0
            if loaded:
                own = int(loaded.get("size_vram", 0) / 2**20)
                if loaded.get("size") and loaded.get("size_vram") == loaded.get("size"):
                    self.needed_mb = int(loaded["size"] / 2**20)
            needed = self.needed_mb or self._estimate()
            if needed is not None and free + own < needed + self.MARGIN_MB:
                self._last = f"vram:{free + own}<{needed}MB"
        except Exception:
            self._last = None
        return self._last


class GpuDemand:
    """Perche' la GPU serve ad altro in questo momento ("fullscreen:<exe>", "app:<exe>", "gaming_mode"), o None."""

    def __init__(self, yield_apps: Iterable[str] = (), ignored_fullscreen: Iterable[str] = DEFAULT_IGNORED_FULLSCREEN,
                 gaming_mode: Callable[[], bool] = lambda: False,
                 notification_state: Callable[[], int | None] = _notification_state,
                 foreground_exe: Callable[[], str | None] = _foreground_exe,
                 processes: Callable[[], set[str]] = running_exes,
                 vram_pressure: Callable[[], str | None] | None = None):
        self.yield_apps = {str(name).strip().lower() for name in yield_apps if str(name).strip()}
        self.ignored = {str(name).lower() for name in ignored_fullscreen}
        self.gaming_mode = gaming_mode
        self.notification_state = notification_state
        self.foreground_exe = foreground_exe
        self.processes = processes
        self.vram_pressure = vram_pressure

    def reason(self) -> str | None:
        if self.gaming_mode():
            return "gaming_mode"
        state = self.notification_state()
        if state in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN):
            exe = self.foreground_exe()
            if state == QUNS_RUNNING_D3D_FULL_SCREEN or (exe and exe not in self.ignored):
                return f"fullscreen:{exe or 'd3d'}"
        if self.yield_apps:
            found = sorted(self.yield_apps & self.processes())
            if found:
                return f"app:{found[0]}"
        if self.vram_pressure is not None:
            return self.vram_pressure()
        return None


class GpuYieldMonitor:
    """Decide quando cedere e riprendere la GPU (con isteresi) e avvisa gli ascoltatori: `listener(yielding, reason)`."""

    def __init__(self, demand: GpuDemand, poll_s: float = POLL_S, enter_s: float = ENTER_S, exit_s: float = EXIT_S,
                 clock: Callable[[], float] = time.monotonic):
        self.demand = demand
        self.poll_s, self.enter_s, self.exit_s = poll_s, enter_s, exit_s
        self.clock = clock
        self.yielding = False
        self.reason: str | None = None
        self._since: float | None = None   # da quando il segnale contrario allo stato attuale e' continuo
        self._listeners: list[Callable[[bool, str | None], None]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.logger = get_logger()

    def add_listener(self, listener: Callable[[bool, str | None], None]) -> None:
        self._listeners.append(listener)
        if self.yielding:
            self._notify(listener)

    def _notify(self, listener: Callable[[bool, str | None], None]) -> None:
        try:
            listener(self.yielding, self.reason)
        except Exception:
            self.logger.exception("GPU yield: ascoltatore fallito")

    def evaluate(self) -> bool:
        """Un passo: legge i segnali, applica l'isteresi; True se lo stato e' cambiato."""
        try:
            reason = self.demand.reason()
        except Exception:
            self.logger.exception("GPU yield: lettura dei segnali fallita")
            reason = None
        now = self.clock()
        wants = reason is not None
        if wants == self.yielding:
            self._since = None
            if wants:
                self.reason = reason
            return False
        if self._since is None:
            self._since = now
        if now - self._since < (self.enter_s if wants else self.exit_s):
            return False
        self._since = None
        self.yielding, self.reason = wants, reason
        self.logger.info("GPU %s (%s)", "ceduta" if wants else "ripresa", reason or "nessun segnale")
        for listener in list(self._listeners):
            self._notify(listener)
        return True

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            while not self._stop.is_set():
                self.evaluate()
                self._stop.wait(self.poll_s)

        self._thread = threading.Thread(target=run, name="gpu-yield", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
