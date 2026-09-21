"""Confini di processo e finestre elevate (F3.2.5).

Windows separa i processi per livello di integrita' (UIPI): un processo NON elevato puo' LEGGERE
l'albero UI Automation di una finestra elevata solo in parte, e non puo' mandarle input simulato
(click, tastiera). Provarci "funziona" senza errori: l'input viene scartato in silenzio. Jake gira
come utente normale e non deve elevarsi da solo (principio del minimo privilegio, vedi la roadmap):
quindi prima di agire su una finestra si chiede se puo' farlo, e se no si dice PERCHE'
invece di lasciare che il click sparisca.

`process_elevation(pid)` ritorna True/False, oppure None quando non si riesce nemmeno a
interrogare il processo (protetto, o di un livello superiore al nostro): per un'azione None si tratta
come "non controllabile", con la spiegazione."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_ELEVATION = 20  # TokenElevation

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]


def process_elevation(pid: int) -> bool | None:
    """True se il processo e' elevato (amministratore), False se no, None se non interrogabile."""
    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            elevation = wintypes.DWORD(0)
            returned = wintypes.DWORD(0)
            ok = _advapi32.GetTokenInformation(
                token, _TOKEN_ELEVATION, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(returned),
            )
            return bool(elevation.value) if ok else None
        finally:
            _kernel32.CloseHandle(token)
    finally:
        _kernel32.CloseHandle(process)


def current_process_is_elevated() -> bool:
    import os

    return bool(process_elevation(os.getpid()))


@dataclass(frozen=True)
class AccessReport:
    can_control: bool
    target_elevated: bool | None
    reason: str


def assess_control(target_pid: int, own_elevated: bool | None = None, target_elevated: bool | None | str = "query") -> AccessReport:
    """Jake puo' pilotare (input simulato) le finestre di `target_pid`? `own_elevated`/`target_elevated`
    si possono passare per provare i casi senza un vero processo elevato."""
    if own_elevated is None:
        own_elevated = current_process_is_elevated()
    elevated = process_elevation(target_pid) if target_elevated == "query" else target_elevated
    if elevated is None:
        return AccessReport(
            False, None,
            "non riesco a leggere il livello di privilegio dell'app (protetta o di livello superiore): "
            "non posso pilotarla in modo affidabile",
        )
    if elevated and not own_elevated:
        return AccessReport(
            False, True,
            "l'app e' in esecuzione come amministratore e Jake no: Windows scarta l'input simulato verso di lei "
            "(UIPI). Jake non si eleva da solo: usa la tastiera tu, oppure avvia l'app senza privilegi elevati",
        )
    return AccessReport(True, elevated, "stesso livello di privilegio: controllo possibile")
