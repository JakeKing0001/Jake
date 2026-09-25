"""Protezione dei test che usano mouse e tastiera VERI contro la fixture Qt.

Trovato eseguendo la suite dentro VS Code: una fixture comparsa DIETRO l'editor aperto ha fatto finire
un click e un testo di prova ("dpi 1.5") dentro un file del progetto aperto nell'editor. Da allora
ogni input reale di questi moduli di test passa da qui: un click e' consentito solo se nel punto c'e'
una finestra della fixture, un tasto solo se la finestra in primo piano e' della fixture. Altrimenti
si prova a riportare la fixture in primo piano e, se non basta, il test fallisce SENZA inviare nulla.

Uso nei moduli di test: `setUpModule = install` e `tearDownModule = uninstall`."""
from __future__ import annotations

import time

# La fixture Qt e il browser isolato di Jake (profilo temporaneo jake_edge_*), mai il browser dell'utente.
_FIXTURE_MARKERS = ("benchmarks.computer_use_fixture", "computer_use_fixture.py", "jake_edge_")
_PATCHED: list[tuple[object, str, object]] = []

_MOUSE_FUNCS = ("click", "doubleClick", "rightClick", "middleClick", "tripleClick", "mouseDown", "mouseUp",
                "dragTo", "drag", "scroll", "vscroll", "hscroll")
_KEY_FUNCS = ("write", "typewrite", "press", "hotkey", "keyDown", "keyUp")


class ForeignWindowInputError(AssertionError):
    """L'input sarebbe finito in una finestra che non e' la fixture: rifiutato."""


_PID_CACHE: dict[str, object] = {"at": 0.0, "pids": set()}


def _fixture_pids() -> set[int]:
    """PID dei processi fixture vivi (cache di mezzo secondo: `write` preme un tasto alla volta)."""
    import psutil

    now = time.monotonic()
    if now - float(_PID_CACHE["at"]) < 0.5:  # type: ignore[arg-type]
        return _PID_CACHE["pids"]  # type: ignore[return-value]
    pids = set()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmdline = " ".join(proc.info.get("cmdline") or [])
        if any(marker in cmdline for marker in _FIXTURE_MARKERS):
            pids.add(proc.info["pid"])
    _PID_CACHE.update(at=now, pids=pids)
    return pids


def _pid_of_hwnd(hwnd) -> int | None:
    import win32process

    try:
        return win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return None


def _bring_a_fixture_to_front(pids: set[int]) -> None:
    import win32gui

    windows = []

    def collect(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and _pid_of_hwnd(hwnd) in pids:
            windows.append(hwnd)
        return True

    win32gui.EnumWindows(collect, None)
    from core.computer_use.ui_automation_adapter import force_foreground_window

    for hwnd in windows[:1]:
        force_foreground_window(hwnd)


def _point_ok(x, y) -> bool:
    import win32gui

    pids = _fixture_pids()
    for _ in range(10):
        if _pid_of_hwnd(win32gui.WindowFromPoint((int(x), int(y)))) in pids:
            return True
        _bring_a_fixture_to_front(pids)
        time.sleep(0.05)
    return False


def _foreground_ok() -> bool:
    import win32gui

    pids = _fixture_pids()
    for _ in range(10):
        if _pid_of_hwnd(win32gui.GetForegroundWindow()) in pids:
            return True
        _bring_a_fixture_to_front(pids)
        time.sleep(0.05)
    return False


def _guard_mouse(name, original):
    def guarded(*args, **kwargs):
        import pyautogui

        x, y = kwargs.get("x"), kwargs.get("y")
        coords = args[1:3] if name in ("scroll", "vscroll", "hscroll") else args[0:2]
        if len(coords) == 2 and all(isinstance(c, (int, float)) for c in coords):
            x, y = coords
        if x is None or y is None:
            x, y = pyautogui.position()
        if not _point_ok(x, y):
            raise ForeignWindowInputError(f"{name}({x}, {y}) rifiutato: nel punto non c'e' la fixture")
        return original(*args, **kwargs)
    return guarded


def _guard_keys(name, original):
    def guarded(*args, **kwargs):
        if not _foreground_ok():
            raise ForeignWindowInputError(f"{name} rifiutato: la finestra in primo piano non e' la fixture")
        return original(*args, **kwargs)
    return guarded


def install() -> None:
    # Per-monitor DPI aware PRIMA di importare pyautogui (che altrimenti degraderebbe il processo).
    from core.win_dpi import ensure_dpi_aware

    ensure_dpi_aware()
    import keyboard
    import pyautogui

    if _PATCHED:
        return
    for name in _MOUSE_FUNCS:
        if hasattr(pyautogui, name):
            original = getattr(pyautogui, name)
            _PATCHED.append((pyautogui, name, original))
            setattr(pyautogui, name, _guard_mouse(name, original))
    for name in _KEY_FUNCS:
        if hasattr(pyautogui, name):
            original = getattr(pyautogui, name)
            _PATCHED.append((pyautogui, name, original))
            setattr(pyautogui, name, _guard_keys(name, original))
    for name in ("write", "press_and_release", "send"):
        original = getattr(keyboard, name)
        _PATCHED.append((keyboard, name, original))
        setattr(keyboard, name, _guard_keys(f"keyboard.{name}", original))


def uninstall() -> None:
    while _PATCHED:
        module, name, original = _PATCHED.pop()
        setattr(module, name, original)
