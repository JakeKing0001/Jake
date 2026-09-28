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


class OllamaClient:
    """Wrapper minimale su /api/chat, /api/embed e /api/tags. Nessuna dipendenza esterna.

    keep_alive lungo di default: il modello resta caldo in VRAM tra un comando vocale e
    l'altro, invece di essere scaricato dopo 5 minuti di silenzio e ricaricato (secondi di
    attesa) alla prossima frase."""

    # Stesso num_ctx per OGNI chiamata allo stesso modello: Ollama ricarica il modello da zero
    # (15-20 s con un 7B) se due richieste consecutive chiedono contesti diversi. Con il
    # classificatore a 8192 e il resto al default (2048) ogni alternanza costava un reload.
    DEFAULT_NUM_CTX = 8192

    def __init__(self, base_url: str | None = None, timeout: float = 30, keep_alive: str = "30m", num_ctx: int | None = None):
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
            "keep_alive": self.keep_alive,
        }
        if format is not None:
            payload["format"] = format
        payload["options"] = {"num_ctx": self.num_ctx, **(options or {})}
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
                "/api/embed", {"model": model, "input": list(inputs), "keep_alive": self.keep_alive},
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
