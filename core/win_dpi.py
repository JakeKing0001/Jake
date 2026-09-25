"""DPI awareness (v3.0). Su schermi con ridimensionamento (125%, 150%...) un processo non
"DPI aware" vede coordinate virtualizzate: lo screenshot (pixel fisici) e il mouse (pixel
logici) non coincidono e "clicca su Accedi" clicca altrove. Dichiarare il processo
per-monitor DPI aware all'avvio allinea screenshot, OCR, pyautogui e win32gui.

Va chiamata PRIMA di importare pyautogui/pyscreeze/mouseinfo: all'import chiamano
`SetProcessDPIAware()`, che porta il processo a "system aware" - verificato su questa macchina
(125%): il processo parte per-monitor (2), dopo `import pyautogui` e' system (1), e con due monitor a
DPI diversi i click calcolati dalle coordinate UI Automation finirebbero fuori bersaglio sul
secondo. Chiamata prima, la per-monitor V2 resta bloccata e l'import non la degrada."""
import ctypes
import sys

UNAWARE, SYSTEM_AWARE, PER_MONITOR_AWARE = 0, 1, 2


def current_dpi_awareness() -> int | None:
    """Consapevolezza DPI EFFETTIVA del thread corrente (0/1/2), None se l'API non esiste."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        return int(user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext()))
    except (AttributeError, OSError, ValueError):
        return None


def ensure_dpi_aware() -> bool:
    if sys.platform != "win32":
        return False
    if not _request_dpi_awareness():
        return False
    # Una chiamata "riuscita" non basta: conta lo stato effettivo (mai dichiarare aware un
    # processo che non lo e').
    return current_dpi_awareness() != UNAWARE


def _request_dpi_awareness() -> bool:
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE_V2 via SetProcessDpiAwarenessContext (Win10 1703+)
        context = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(context):
            return True
    except (AttributeError, OSError):
        pass
    try:
        # SetProcessDpiAwareness ritorna un HRESULT, non solleva se fallisce (es.
        # E_ACCESSDENIED se la DPI awareness e' gia' stata impostata, dal manifest o da una
        # chiamata precedente nello stesso processo): un valore diverso da S_OK (0) qui veniva
        # ignorato e la funzione riportava comunque True, l'esatto genere di falso positivo che
        # questo modulo esiste per evitare (schermata e mouse disallineati, "clicca su Accedi"
        # clicca altrove) - riprodotto per davvero chiamando l'API due volte di fila.
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return True
    except (AttributeError, OSError):
        pass
    try:
        # SetProcessDPIAware ritorna un BOOL (0 = fallito), stesso principio di sopra.
        if ctypes.windll.user32.SetProcessDPIAware():
            return True
    except (AttributeError, OSError):
        pass
    return False
