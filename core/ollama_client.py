"""Client HTTP condiviso per Ollama (v3.0).

Perche' esiste: prima ogni componente (classificatore, planner, embedding, visione, skill
LLM) parlava con Ollama per conto suo, tutti con "http://localhost:11434" hardcoded. Su
Windows "localhost" viene risolto PRIMA come ::1 (IPv6), dove Ollama non ascolta: ogni
chiamata pagava ~2 secondi di timeout prima di ripiegare su 127.0.0.1. Centralizzare
l'indirizzo (127.0.0.1 esplicito, o JAKE_OLLAMA_URL) ha tolto quei 2 secondi da OGNI
richiesta: e' la singola ottimizzazione di latenza piu' grande della 3.0."""
import json
import os
from urllib import error, request

from core.turn_cancellation import cancellable_call

DEFAULT_BASE_URL = (
    os.environ.get("JAKE_OLLAMA_URL")
    or (f"http://{os.environ['OLLAMA_HOST']}" if os.environ.get("OLLAMA_HOST", "").count(":") == 1
        and not os.environ["OLLAMA_HOST"].startswith("http") else os.environ.get("OLLAMA_HOST"))
    or "http://127.0.0.1:11434"
).rstrip("/")


class OllamaError(Exception):
    """Errore generico nel dialogo con Ollama."""


class OllamaUnavailable(OllamaError):
    """Ollama non raggiungibile (server spento, porta diversa, rete)."""


class OllamaResponseError(OllamaError):
    """Ollama ha risposto, ma con qualcosa di inatteso (modello mancante, JSON non valido)."""


class OllamaTimeout(OllamaUnavailable):
    """Il server risponde ma il modello non ha finito in tempo (troppo lento: vedi core/model_health.py)."""


class OllamaModelMissing(OllamaResponseError):
    """Il modello richiesto non e' installato."""


def _failure_error(failure) -> OllamaError:
    message = failure.detail + (f" ({failure.hint})" if failure.hint else "")
    return OllamaTimeout(message) if failure.kind == "timeout" else OllamaUnavailable(message)


PRIMARY_KEEP_ALIVE = "30m"
SECONDARY_KEEP_ALIVE = "2m"
LOW_MEMORY_PRIMARY_KEEP_ALIVE = "5m"
LOW_MEMORY_SECONDARY_KEEP_ALIVE = "0"

_settings_cache: dict | None = None


def _settings() -> dict:
    """Le poche chiavi che servono alla politica di memoria, lette una volta sola."""
    global _settings_cache
    if _settings_cache is None:
        try:
            from core.config import Config

            config = Config()
            _settings_cache = {
                "primary": config.get("ollama_model", "qwen2.5:7b"),
                "low_memory": str(config.get("low_memory", False)).lower() in {"1", "true", "yes", "on"},
                "primary_keep_alive": config.get("ollama_keep_alive"),
                "secondary_keep_alive": config.get("ollama_secondary_keep_alive"),
                # quanta VRAM usa il modello principale mentre e' caricato (core/ollama_gpu_budget.py)
                "gpu": {key: config.get(key) for key in ("low_memory", "ollama_gpu_budget_mb", "ollama_gpu_budget_mode",
                                                          "ollama_num_gpu_layers", "ollama_context")},
            }
        except Exception:
            _settings_cache = {"primary": "qwen2.5:7b", "low_memory": False}
    return _settings_cache


_gpu_policy = None
_gpu_policy_lock = __import__("threading").Lock()


def gpu_policy():
    """La politica dei layer GPU del modello principale, una per processo (core/ollama_gpu_budget.py)."""
    global _gpu_policy
    with _gpu_policy_lock:
        if _gpu_policy is None:
            from core.ollama_gpu_budget import GpuLayerPolicy, budget_from_settings

            settings = _settings()
            budget = budget_from_settings(settings.get("gpu") or {})
            _gpu_policy = GpuLayerPolicy(budget, settings.get("primary", "qwen2.5:7b"),
                                         budget.context or OllamaClient.DEFAULT_NUM_CTX, DEFAULT_BASE_URL)
        return _gpu_policy


def runtime_options(model: str | None, options: dict | None = None) -> dict:
    """UNICO punto in cui si decidono le opzioni di runtime di una chiamata a Ollama.

    Le opzioni del chiamante (temperature, num_predict, num_ctx...) restano. Per il modello PRINCIPALE si aggiungono
    `num_gpu` (budget di VRAM) e, se configurato, `ollama_context` come `num_ctx`: due chiamate allo stesso modello
    con valori diversi farebbero ricaricare il modello da Ollama, quindi qui valgono per tutte. Il chiamante non
    sceglie `num_gpu`. Visione, coding ed embedding restano invariati (per loro conta keep_alive_for)."""
    merged = dict(options or {})
    settings = _settings()
    if not model or model != settings.get("primary"):
        return merged
    policy = gpu_policy()
    if policy.budget.context is not None:
        merged["num_ctx"] = policy.budget.context
    layers = policy.layers()
    if layers is None:
        merged.pop("num_gpu", None)
    else:
        merged["num_gpu"] = layers
    return merged


def keep_alive_for(model: str | None) -> str:
    """Quanto a lungo Ollama tiene in RAM/VRAM un modello dopo una richiesta.

    Solo il modello di conversazione principale resta caldo a lungo (la prossima frase vocale
    non deve aspettare 15-20 s di ricaricamento). Visione, coding ed embedding vengono usati di
    rado: tenerli 30 minuti voleva dire fino a ~15 GB occupati per una sola domanda sullo
    schermo. `low_memory` accorcia entrambi; `ollama_keep_alive` e
    `ollama_secondary_keep_alive` in settings.json sovrascrivono i valori."""
    settings = _settings()
    low = settings.get("low_memory", False)
    # GPU ceduta (core/gpu_yield.py): il modello principale gira su CPU e non deve restare in RAM a lungo
    yielding = _gpu_policy is not None and _gpu_policy.yielding
    if model and model == settings.get("primary") and not yielding:
        return settings.get("primary_keep_alive") or (LOW_MEMORY_PRIMARY_KEEP_ALIVE if low else PRIMARY_KEEP_ALIVE)
    return settings.get("secondary_keep_alive") or (LOW_MEMORY_SECONDARY_KEEP_ALIVE if low else SECONDARY_KEEP_ALIVE)


class OllamaClient:
    """Wrapper minimale su /api/chat, /api/embed e /api/tags. Nessuna dipendenza esterna.

    keep_alive lungo di default: il modello resta caldo in VRAM tra un comando vocale e
    l'altro, invece di essere scaricato dopo 5 minuti di silenzio e ricaricato (secondi di
    attesa) alla prossima frase."""

    # Stesso num_ctx per OGNI chiamata allo stesso modello: Ollama ricarica il modello da zero
    # (15-20 s con un 7B) se due richieste consecutive chiedono contesti diversi. Con il
    # classificatore a 8192 e il resto al default (2048) ogni alternanza costava un reload.
    DEFAULT_NUM_CTX = 8192

    def __init__(self, base_url: str | None = None, timeout: float = 30, keep_alive: str | None = None, num_ctx: int | None = None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx or self.DEFAULT_NUM_CTX

    # ---- basso livello -------------------------------------------------------------

    @staticmethod
    def _read(target, timeout: float) -> bytes:
        # Dentro un turno vocale annullabile gira su un thread a parte (cancellable_call): "Jake,
        # basta" smette di aspettare il modello subito, la risposta tardiva viene scartata.
        with request.urlopen(target, timeout=timeout) as response:
            return response.read()

    def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        from core import model_health

        failed = model_health.failure()
        if failed is not None:
            # stesso turno, stesso problema: non si ripete la stessa attesa (prova reale del 27/09/2026)
            raise _failure_error(failed)
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}{path}", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with model_health.calling():
                parsed = json.loads(cancellable_call(self._read, http_request, timeout or self.timeout, name="jake-ollama-http").decode("utf-8"))
        except error.HTTPError as exc:
            if model_health.classify(exc) == model_health.MODEL_MISSING:
                raise OllamaModelMissing(f"modello non installato: {payload.get('model', '')}") from exc
            raise OllamaResponseError(f"HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            failed = model_health.diagnose(self.base_url, model_health.classify(exc))
            model_health.record(failed)
            raise _failure_error(failed) from exc
        except json.JSONDecodeError as exc:
            raise OllamaResponseError("risposta non JSON") from exc
        # F1: un corpo JSON valido ma non un dizionario (proxy/porta sbagliata che risponde con
        # qualcosa di JSON ma non l'API di Ollama) faceva propagare un AttributeError da ogni
        # chiamante (chat_text/embed/list_models fanno tutti payload.get(...) subito dopo),
        # invece del solo OllamaError che i chiamanti gia' catturano.
        if not isinstance(parsed, dict):
            raise OllamaResponseError("risposta non nella forma attesa (non un dizionario)")
        model_health.succeeded()
        return parsed

    def _get(self, path: str, timeout: float | None = None) -> dict:
        try:
            parsed = json.loads(cancellable_call(self._read, f"{self.base_url}{path}", timeout or self.timeout, name="jake-ollama-http").decode("utf-8"))
        except error.HTTPError as exc:
            raise OllamaResponseError(f"HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise OllamaUnavailable(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise OllamaResponseError("risposta non JSON") from exc
        if not isinstance(parsed, dict):
            raise OllamaResponseError("risposta non nella forma attesa (non un dizionario)")
        return parsed

    # ---- API comode ------------------------------------------------------------------

    def chat(self, model: str, messages: list, format=None, options: dict | None = None,
             timeout: float | None = None, images: list | None = None) -> dict:
        payload = {
            "model": model,
            "stream": False,
            "messages": messages,
            "keep_alive": self.keep_alive or keep_alive_for(model),
        }
        if format is not None:
            payload["format"] = format
        payload["options"] = runtime_options(model, {"num_ctx": self.num_ctx, **(options or {})})
        observer = getattr(self, "on_chat", None)
        if observer is None:
            return self._post("/api/chat", payload, timeout=timeout)
        # F8.4.7: successo e latenza REALI di ogni chiamata, per chi sceglie i modelli (JakeCore -> ModelRouter)
        import time as _time

        started = _time.monotonic()
        try:
            result = self._post("/api/chat", payload, timeout=timeout)
        except OllamaError:
            _notify(observer, model, False, (_time.monotonic() - started) * 1000)
            raise
        _notify(observer, model, True, (_time.monotonic() - started) * 1000)
        return result

    def chat_text(self, model: str, messages: list, options: dict | None = None, timeout: float | None = None) -> str | None:
        """Come chat(), ma restituisce direttamente il testo (None se vuoto/errore)."""
        try:
            result = self.chat(model, messages, options=options, timeout=timeout)
        except OllamaError:
            return None
        message = result.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return content.strip() if isinstance(content, str) and content.strip() else None

    def embed(self, model: str, inputs: list[str], timeout: float | None = None) -> list[list[float]] | None:
        """Embedding di piu' testi in una chiamata. None se Ollama/modello non disponibili."""
        if not inputs:
            return []
        try:
            payload = self._post(
                "/api/embed", {"model": model, "input": list(inputs), "keep_alive": self.keep_alive or keep_alive_for(model)},
                timeout=timeout,
            )
        except OllamaError:
            return None
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(inputs):
            return None
        return embeddings

    def list_models(self) -> list[str] | None:
        try:
            payload = self._get("/api/tags", timeout=5)
        except OllamaError:
            return None
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            return None
        return [model.get("name", "") for model in raw_models if isinstance(model, dict)]

    def is_available(self) -> bool:
        return self.list_models() is not None

    def loaded_models(self) -> list[str] | None:
        """F8.4.4: i modelli caricati in memoria adesso (/api/ps). None se Ollama non risponde."""
        try:
            payload = self._get("/api/ps", timeout=5)
        except OllamaError:
            return None
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            return None
        return [model.get("name", "") for model in raw_models if isinstance(model, dict)]

    def unload(self, model: str) -> None:
        """F8.4.4: libera subito la memoria (VRAM/RAM) di un modello. L'API REST di Ollama non ha un comando di
        scaricamento dedicato: una richiesta senza prompt con keep_alive 0 scarica il modello e basta."""
        self._post("/api/generate", {"model": model, "keep_alive": 0}, timeout=30)

    def preload(self, model: str) -> None:
        """Carica il modello senza generare, con le STESSE opzioni delle chiamate vere (un num_ctx o num_gpu diverso
        farebbe ricaricare il modello alla prima domanda)."""
        self._post("/api/generate", {"model": model, "keep_alive": self.keep_alive or keep_alive_for(model),
                                     "options": runtime_options(model, {"num_ctx": self.num_ctx})}, timeout=300)

    def has_model(self, name: str) -> bool:
        models = self.list_models() or []
        base = name.split(":")[0]
        return any(model == name or model.split(":")[0] == base and ":" not in name for model in models)

    def pick_model(self, preferred: str, fallback: str) -> str:
        """Il modello preferito se scaricato, altrimenti il fallback (es. coder -> generico)."""
        models = self.list_models()
        if models is None:
            return fallback
        if preferred in models or any(m.split(":")[0] == preferred.split(":")[0] for m in models):
            return preferred
        return fallback


def _notify(observer, model: str, success: bool, latency_ms: float) -> None:
    try:
        observer(model, success, latency_ms)
    except Exception:
        pass  # un osservatore guasto non deve mai far fallire una chiamata al modello
