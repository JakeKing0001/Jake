"""Budget di VRAM per il modello Ollama principale: stesso modello, solo una parte dei layer sulla GPU.

Ollama tiene di default TUTTI i layer di qwen2.5:7b sulla GPU (~5 GB su una RTX da 8 GB, accanto a Whisper e HUD).
L'opzione `num_gpu` delle richieste dice quanti layer stanno sulla GPU; il resto gira su CPU/RAM. Quanta VRAM costa
un layer dipende da modello, quantizzazione, context, driver e versione di Ollama, quindi qui non si indovina: si
MISURA la VRAM attribuita al modello da `/api/ps` (`size_vram`, mai il totale di nvidia-smi che include Whisper,
Windows e il browser) e si sceglie il numero di layer piu' alto che sta nel budget.

Misura reale del 04/10/2026 (RTX 4060 Laptop, Ollama 0.35.1, qwen2.5:7b Q4_K_M, 28 blocchi):
  GPU piena 4987 MB, 51 tok/s | 3 layer 987 MB, ~7 tok/s | 0 layer (solo CPU) 0 MB, ~7,4 tok/s.
Sotto ~1 GB la generazione e' limitata dalla CPU: pochi layer sulla GPU non la accelerano. Il budget e' una scelta
dell'utente (memoria video libera per altro), non un'ottimizzazione di velocita'.

Configurazione (config/settings.json):
  ollama_gpu_budget_mb    VRAM massima del modello principale; assente = comportamento di Ollama (GPU piena).
                          Con `low_memory: true` e nessun valore esplicito vale 1024.
  ollama_gpu_budget_mode  "hard" (default): mai sopra il budget, anche a costo della sola CPU.
                          "soft": privilegia la velocita' e tollera fino al 20% in piu'.
  ollama_num_gpu_layers   "auto" (default, calibrato) oppure un numero fisso di layer.
  ollama_context          context del modello principale per TUTTE le chiamate (assente = quello del chiamante).

La calibrazione (poche cariche del modello, ~30 s, una volta) si salva in data/ollama_gpu_calibration.json per
modello + digest + GPU + budget + modo + context: se uno cambia si ricalibra. Finche' non c'e' una calibrazione
valida il modello gira su CPU (num_gpu 0), che non supera mai il budget."""
from __future__ import annotations

import json
import math
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib import request

from core.logger import get_logger

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "ollama_gpu_calibration.json"
LOW_MEMORY_BUDGET_MB = 1024
SOFT_TOLERANCE = 1.2
FULL_GPU_LAYERS = 999   # "tutti i layer": cio' che Ollama farebbe da solo
CALIBRATION_KEEP_ALIVE = "20s"


@dataclass(frozen=True)
class GpuBudget:
    budget_mb: int | None = None
    mode: str = "hard"
    fixed_layers: int | None = None
    context: int | None = None

    @property
    def enabled(self) -> bool:
        return self.budget_mb is not None or self.fixed_layers is not None

    @property
    def limit_mb(self) -> float:
        if self.budget_mb is None:
            return math.inf
        return self.budget_mb * (SOFT_TOLERANCE if self.mode == "soft" else 1.0)


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def budget_from_settings(values: dict) -> GpuBudget:
    """Dalle impostazioni: un valore malformato vale "non impostato", mai un errore all'avvio."""
    low_memory = str(values.get("low_memory", False)).lower() in {"1", "true", "yes", "on"}
    budget = _positive_int(values.get("ollama_gpu_budget_mb"))
    if budget is None and low_memory:
        budget = LOW_MEMORY_BUDGET_MB
    mode = str(values.get("ollama_gpu_budget_mode") or "hard").strip().lower()
    layers_raw = values.get("ollama_num_gpu_layers")
    fixed = None
    if layers_raw not in (None, "", "auto"):
        try:
            fixed = max(0, int(layers_raw))
        except (TypeError, ValueError):
            fixed = None
    return GpuBudget(budget_mb=budget, mode="soft" if mode == "soft" else "hard", fixed_layers=fixed,
                     context=_positive_int(values.get("ollama_context")))


def gpu_name(run=subprocess.run) -> str:
    """Nome e memoria della GPU NVIDIA (parte della chiave di calibrazione); "none" senza GPU NVIDIA."""
    try:
        out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True,
                  text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (out.stdout or "").strip().splitlines()
        return line[0].strip() if out.returncode == 0 and line else "none"
    except (OSError, subprocess.SubprocessError):
        return "none"


class OllamaApi:
    """Le quattro chiamate che servono alla calibrazione (sostituibili nei test)."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _call(self, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"})
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")

    def ps(self) -> list[dict]:
        return list(self._call("/api/ps").get("models", []))

    def tags(self) -> list[dict]:
        return list(self._call("/api/tags").get("models", []))

    def show(self, model: str) -> dict:
        return self._call("/api/show", {"model": model})

    def load(self, model: str, options: dict) -> None:
        self._call("/api/generate", {"model": model, "prompt": "", "keep_alive": CALIBRATION_KEEP_ALIVE,
                                     "options": options}, timeout=300)

    def unload(self, model: str) -> None:
        self._call("/api/generate", {"model": model, "keep_alive": 0}, timeout=60)


def _same_model(entry: dict, model: str) -> bool:
    name = str(entry.get("name") or entry.get("model") or "")
    return name == model or name == f"{model}:latest"


def model_vram_mb(ps_models: list[dict], model: str) -> int | None:
    """VRAM attribuita da Ollama al SOLO modello (`size_vram`), in MB; None se non e' caricato."""
    for entry in ps_models:
        if isinstance(entry, dict) and _same_model(entry, model):
            vram = entry.get("size_vram")
            return round(vram / 1e6) if isinstance(vram, (int, float)) else None
    return None


def model_size_mb(ps_models: list[dict], model: str) -> int | None:
    """Dimensione totale del modello caricato (pesi + context) secondo Ollama, in MB."""
    for entry in ps_models:
        if isinstance(entry, dict) and _same_model(entry, model):
            size = entry.get("size")
            return round(size / 1e6) if isinstance(size, (int, float)) else None
    return None


def model_digest(tags: list[dict], model: str) -> str | None:
    for entry in tags:
        if isinstance(entry, dict) and _same_model(entry, model):
            digest = entry.get("digest")
            return str(digest) if digest else None
    return None


def layer_count(show: dict) -> int | None:
    """Blocchi del modello da /api/show (`<famiglia>.block_count`); Ollama conta anche il layer di uscita."""
    info = show.get("model_info") if isinstance(show, dict) else None
    if not isinstance(info, dict):
        return None
    for key, value in info.items():
        if key.endswith(".block_count") and isinstance(value, int):
            return value + 1
    return None


class Calibrator:
    def __init__(self, api: OllamaApi, model: str, budget: GpuBudget, num_ctx: int, gpu: str,
                 cache_path: Path = CACHE_PATH, clock: Callable[[], float] = time.time, logger=None):
        self.api, self.model, self.budget, self.num_ctx, self.gpu = api, model, budget, num_ctx, gpu
        self.cache_path = cache_path
        self.clock = clock
        self.logger = logger or get_logger()
        self.sizes: dict[int, int] = {}

    def key(self, digest: str | None) -> dict:
        return {"model": self.model, "digest": digest, "gpu": self.gpu, "budget_mb": self.budget.budget_mb,
                "mode": self.budget.mode, "num_ctx": self.num_ctx}

    def _read_cache(self) -> dict:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def cached(self, digest: str | None) -> dict | None:
        """Il risultato salvato SOLO se modello, digest, GPU, budget, modo e context coincidono."""
        entry = self._read_cache().get(self.model)
        if isinstance(entry, dict) and entry.get("key") == self.key(digest) and isinstance(entry.get("num_gpu"), int):
            return entry
        return None

    def _save(self, digest: str | None, result: dict) -> None:
        data = self._read_cache()
        data[self.model] = {"key": self.key(digest), **result, "at": self.clock()}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def measure(self, layers: int) -> int:
        """Carica il modello con `layers` sulla GPU e legge la VRAM che Ollama gli attribuisce."""
        self.api.unload(self.model)
        self.api.load(self.model, {"num_gpu": layers, "num_ctx": self.num_ctx})
        models = self.api.ps()
        vram = model_vram_mb(models, self.model)
        size = model_size_mb(models, self.model)
        if size is not None:
            self.sizes[layers] = size
        if vram is None:
            raise RuntimeError(f"{self.model} non risulta caricato dopo la richiesta")
        self.logger.info("Calibrazione GPU %s: %d layer -> %d MB di VRAM", self.model, layers, vram)
        return vram

    def calibrate(self) -> dict:
        """Il numero di layer piu' alto la cui VRAM misurata sta nel limite. Stima lineare da due misure (1 layer e
        pochi layer: c'e' una base fissa appena un layer va sulla GPU) e poi verifica: 3-5 cariche in tutto."""
        digest = model_digest(self.api.tags(), self.model)
        total = layer_count(self.api.show(self.model)) or 29
        limit = self.budget.limit_mb
        measured: dict[int, int] = {}
        try:
            measured[1] = self.measure(1)
            if measured[1] > limit:
                best = 0
            else:
                probe = min(5, total)
                measured[probe] = self.measure(probe)
                per_layer = max(1.0, (measured[probe] - measured[1]) / max(1, probe - 1))
                estimate = max(1, min(total, 1 + int((limit - measured[1]) // per_layer)))
                best = estimate
                if estimate not in measured:
                    measured[estimate] = self.measure(estimate)
                while best > 0 and measured.get(best, 0) > limit:
                    best -= 1
                    if best > 0 and best not in measured:
                        measured[best] = self.measure(best)
                # una sola prova verso l'alto se la stima ha lasciato margine
                if 0 < best < total and measured[best] + per_layer <= limit and best + 1 not in measured:
                    measured[best + 1] = self.measure(best + 1)
                    if measured[best + 1] <= limit:
                        best += 1
        finally:
            try:
                self.api.unload(self.model)   # la prossima richiesta vera lo carica con il valore scelto
            except Exception:
                self.logger.exception("Impossibile scaricare %s dopo la calibrazione", self.model)
        result = {"num_gpu": best, "vram_mb": measured.get(best, 0), "layers_total": total,
                  "size_mb": self.sizes.get(best) or max(self.sizes.values(), default=0),
                  "measured": {str(k): v for k, v in sorted(measured.items())}}
        self._save(digest, result)
        self.logger.info("Calibrazione GPU %s: %d/%d layer sulla GPU, ~%d MB (budget %s MB, %s)", self.model, best,
                         total, result["vram_mb"], self.budget.budget_mb, self.budget.mode)
        return result


class GpuLayerPolicy:
    """Quanti layer del modello principale vanno sulla GPU, deciso una volta per processo. Senza budget: None
    (nessun `num_gpu`, comportamento di sempre). Finche' la calibrazione non e' pronta: 0 (solo CPU, mai sopra il
    budget); la calibrazione parte in background al primo bisogno o da `start()`."""

    WAIT_FOR_CALIBRATION_S = 90.0

    def __init__(self, budget: GpuBudget, model: str, num_ctx: int, base_url: str,
                 api: OllamaApi | None = None, gpu: Callable[[], str] = gpu_name, cache_path: Path = CACHE_PATH):
        self.budget, self.model, self.num_ctx = budget, model, num_ctx
        self.api = api or OllamaApi(base_url)
        self._gpu = gpu
        self.cache_path = cache_path
        self._layers: int | None = None
        self._resolved = False
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self.result: dict | None = None
        self.logger = get_logger()

    def _calibrator(self) -> Calibrator:
        return Calibrator(self.api, self.model, self.budget, self.num_ctx, self._gpu(), self.cache_path)

    def start(self, force: bool = False) -> None:
        """Usa una calibrazione salvata valida, altrimenti calibra in background."""
        if not self.budget.enabled:
            self._finish(None)
            return
        if self.budget.fixed_layers is not None:
            self._finish(self.budget.fixed_layers)
            return
        with self._lock:
            if self._thread is not None or self._resolved:
                return
            self._thread = threading.Thread(target=self._resolve, args=(force,), name="ollama-gpu-calibration",
                                            daemon=True)
            self._thread.start()

    def _resolve(self, force: bool) -> None:
        layers = 0
        try:
            calibrator = self._calibrator()
            if calibrator.gpu == "none":
                layers = 0
            else:
                digest = model_digest(self.api.tags(), self.model)
                cached = None if force else calibrator.cached(digest)
                self.result = cached or calibrator.calibrate()
                layers = int(self.result["num_gpu"])
        except Exception:
            self.logger.exception("Calibrazione GPU di %s non riuscita: resto su CPU (num_gpu 0)", self.model)
            layers = 0
        self._finish(layers)

    def _finish(self, layers: int | None) -> None:
        self._layers = layers
        self._resolved = True
        self._done.set()

    def layers(self, wait: bool = True) -> int | None:
        """Il valore da mettere in `num_gpu`. Aspetta (una volta, al massimo WAIT_FOR_CALIBRATION_S) la calibrazione
        in corso: una richiesta con un valore diverso ricaricherebbe il modello a meta' misura."""
        if not self._resolved:
            self.start()
            if wait:
                self._done.wait(self.WAIT_FOR_CALIBRATION_S)
        if not self._resolved:
            return 0 if self.budget.enabled else None
        return self._layers
