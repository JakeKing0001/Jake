"""DPI awareness (v3.0). Su schermi con ridimensionamento (125%, 150%...) un processo non
"DPI aware" vede coordinate virtualizzate: lo screenshot (pixel fisici) e il mouse (pixel
logici) non coincidono e "clicca su Accedi" clicca altrove. Dichiarare il processo
per-monitor DPI aware all'avvio allinea screenshot, OCR, pyautogui e win32gui."""
import ctypes
import sys


def ensure_dpi_aware() -> bool:
    if sys.platform != "win32":
        return False
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE_V2 via SetProcessDpiAwarenessContext (Win10 1703+)
        context = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(context):
            return True
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except (AttributeError, OSError):
        return False
