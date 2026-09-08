"""Client per l'API REST di Home Assistant (v5.7, Home/IoT): l'hub domotico self-hosted piu'
diffuso, con l'API piu' stabile e documentata (token bearer, GET /api/states, POST /api/
services/<domain>/<service> - https://developers.home-assistant.io/docs/api/rest/) tra le
alternative. Preferito a un protocollo diretto (Zigbee/Z-Wave/Matter) perche' quelli
richiederebbero un ponte hardware specifico che Jake non ha modo di possedere: chi ha gia' un
hub Home Assistant (comunissimo in ambito domotico self-hosted) puo' collegare Jake senza
hardware aggiuntivo, tramite HTTP.

Stesso principio di core/ollama_client.py: solo libreria standard (urllib), nessuna dipendenza
esterna in piu' da installare.

Nessun hub/dispositivo reale disponibile in questo ambiente per verificarlo end-to-end: i test
(tests/test_home_assistant_client.py) mockano urllib.request.urlopen verificando che le
richieste abbiano la forma esatta documentata dall'API ufficiale, lo stesso confine gia' usato
per NestClient/OllamaClient (mai un vero nest-cli o server Ollama nei test, solo il contratto)."""
import json
from urllib import error, request

DEFAULT_TIMEOUT = 10


class HomeAssistantError(Exception):
    """Errore generico nel dialogo con Home Assistant."""


class HomeAssistantUnavailable(HomeAssistantError):
    """Home Assistant non raggiungibile (URL sbagliato, hub spento, rete)."""


class HomeAssistantClient:
    def __init__(self, base_url: str = None, token: str = None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip() or None
        self.timeout = timeout

    def is_available(self) -> bool:
        """Vero se URL e token sono entrambi configurati. Non fa una vera chiamata di rete (a
        differenza di OllamaClient.is_available): una richiesta di stato in piu' a ogni comando
        vocale per un hub che quasi nessun utente ha configurato non vale il costo di latenza."""
        return bool(self.base_url) and bool(self.token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _request(self, path: str, method: str, payload: dict = None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        http_request = request.Request(f"{self.base_url}{path}", data=body, headers=self._headers(), method=method)
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else []
        except error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            raise HomeAssistantError(f"HTTP {exc.code}: {detail}") from exc
        except (error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise HomeAssistantUnavailable(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise HomeAssistantError("risposta non JSON") from exc

    def list_states(self, domain: str = None) -> list[dict]:
        """Stato di tutte le entita' note (luci, prese, sensori...), opzionalmente filtrate per
        dominio (es. 'light', 'switch': il prefisso di entity_id prima del punto)."""
        states = self._request("/api/states", "GET")
        if domain:
            states = [state for state in states if state.get("entity_id", "").startswith(f"{domain}.")]
        return states

    def get_state(self, entity_id: str) -> dict:
        return self._request(f"/api/states/{entity_id}", "GET")

    def call_service(self, domain: str, service: str, entity_id: str = None, **extra_data) -> list:
        """Chiama un servizio Home Assistant, es. call_service('light', 'turn_on', 'light.
        soggiorno', brightness=200): il modo standard con cui l'API REST fa AGIRE un dispositivo
        (a differenza di list_states/get_state, che leggono soltanto)."""
        payload = dict(extra_data)
        if entity_id:
            payload["entity_id"] = entity_id
        return self._request(f"/api/services/{domain}/{service}", "POST", payload)
