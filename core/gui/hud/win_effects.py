"""Effetti nativi di Windows per l'HUD: blur acrilico dietro la finestra, angoli arrotondati,
click-through, non attivazione. Tutto via ctypes, nessuna dipendenza aggiuntiva; su sistemi
dove un'API manca (Windows 10 vecchi) ogni funzione fallisce in silenzio e l'HUD resta
semplicemente una finestra traslucida senza blur."""
import ctypes
import sys
from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWA_EXCLUDED_FROM_PEEK = 12


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class _WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _is_windows() -> bool:
    return sys.platform == "win32"


def rgba_to_abgr(r: int, g: int, b: int, a: int) -> int:
    return (a << 24) | (b << 16) | (g << 8) | r


def apply_acrylic(hwnd: int, tint=(16, 30, 60, 140), enabled: bool = True) -> bool:
    """Blur acrilico (Windows 10 1803+/11) con tinta RGBA dietro l'intera finestra."""
    if not _is_windows():
        return False
    try:
        accent = _ACCENT_POLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND if enabled else ACCENT_DISABLED
        accent.AccentFlags = 0x20 | 0x40 | 0x80 | 0x100 if enabled else 0
        accent.GradientColor = rgba_to_abgr(*tint)
        data = _WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.addressof(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def set_rounded_corners(hwnd: int) -> bool:
    if not _is_windows():
        return False
    try:
        preference = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(preference), ctypes.sizeof(preference)
        )
        excluded = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_EXCLUDED_FROM_PEEK, ctypes.byref(excluded), ctypes.sizeof(excluded)
        )
        return True
    except Exception:
        return False


def _get_exstyle(hwnd: int) -> int:
    user32 = ctypes.windll.user32
    getter = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    getter.restype = ctypes.c_ssize_t
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    return getter(wintypes.HWND(hwnd), GWL_EXSTYLE)


def _set_exstyle(hwnd: int, style: int) -> None:
    user32 = ctypes.windll.user32
    setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    setter.restype = ctypes.c_ssize_t
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    setter(wintypes.HWND(hwnd), GWL_EXSTYLE, style)


def set_click_through(hwnd: int, enabled: bool) -> bool:
    """Con enabled=True i click passano alla finestra sotto: l'HUD vocale non deve mai
    rubare un click all'app su cui l'utente sta lavorando."""
    if not _is_windows():
        return False
    try:
        style = _get_exstyle(hwnd)
        if enabled:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT
        _set_exstyle(hwnd, style)
        return True
    except Exception:
        return False


def set_no_activate(hwnd: int, enabled: bool) -> bool:
    """WS_EX_NOACTIVATE: mostrare l'HUD non toglie il focus alla finestra attiva."""
    if not _is_windows():
        return False
    try:
        style = _get_exstyle(hwnd)
        style = (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) if enabled else (style & ~WS_EX_NOACTIVATE)
        _set_exstyle(hwnd, style)
        return True
    except Exception:
        return False
