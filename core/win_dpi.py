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
