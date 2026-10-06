"""VRAM libera della GPU NVIDIA letta da NVML (la libreria del driver), senza lanciare `nvidia-smi`.

Il monitor della GPU (core/gpu_yield.py) la legge ogni 10 s: `nvidia-smi` costava ~60 ms e un processo a ogni lettura,
NVML ~12 ms una volta sola e poi praticamente nulla (misura del 06/10/2026, stessi valori di nvidia-smi). Se NVML non
si carica (niente driver NVIDIA, altra piattaforma) si torna a `nvidia-smi`; se manca anche quello, None."""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading

_lock = threading.Lock()
_handle: ctypes.c_void_p | None = None
_library = None
_unavailable = False


class _MemoryV2(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong), ("reserved", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


def _nvml():
    global _handle, _library, _unavailable
    with _lock:
        if _handle is not None or _unavailable:
            return _library, _handle
        try:
            path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll")
            library = ctypes.WinDLL(path) if os.path.exists(path) else ctypes.WinDLL("nvml.dll")
            if library.nvmlInit_v2() != 0:
                raise OSError("nvmlInit_v2")
            handle = ctypes.c_void_p()
            if library.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
                raise OSError("nvmlDeviceGetHandleByIndex_v2")
            _library, _handle = library, handle
        except (OSError, AttributeError):
            _unavailable = True
        return _library, _handle


def _nvidia_smi_free_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(out.stdout.split()[0]) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


def free_vram_mb() -> int | None:
    """MB liberi sulla prima GPU NVIDIA (come `nvidia-smi --query-gpu=memory.free`), None se non si possono leggere."""
    library, handle = _nvml()
    if library is not None and handle is not None:
        memory = _MemoryV2()
        memory.version = ctypes.sizeof(_MemoryV2) | (2 << 24)
        try:
            if library.nvmlDeviceGetMemoryInfo_v2(handle, ctypes.byref(memory)) == 0:
                return int(memory.free // 2**20)
        except (OSError, AttributeError):
            pass
    return _nvidia_smi_free_mb()
