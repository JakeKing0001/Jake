import json
from urllib import error, request

from core.planner import Plan, PlanStep
from core.ollama_client import DEFAULT_BASE_URL


class PlannerProvider:
    """Scompone una richiesta multi-step in una sequenza ordinata di comandi, usando Ollama.

    Usato solo come seconda scelta quando il router a singolo intent non riconosce la
    richiesta (UNKNOWN): non rallenta i comandi semplici, gia' gestiti dal percorso normale."""

    def __init__(
        self,
        registry,
        base_url: str = None,
        timeout: float = 40,
        model: str = None,
        context_provider=None,
    ):
        self.registry = registry
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.model = model or "qwen2.5:7b"
        self.last_error = None
        self.context_provider = context_provider

    def build_plan(self, text: str) -> Plan | None:
        self.last_error = None
        try:
            response = self._request_ollama(text)
            payload = self._parse_response(response)
            return self._plan_from_payload(payload)
        except error.URLError:
            self.last_error = "OLLAMA_UNAVAILABLE"
        except (TimeoutError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self.last_error = "INVALID_PLAN_RESPONSE"
        except Exception:
            self.last_error = "PLANNER_ERROR"
        return None

    def _valid_intents(self) -> list[str]:
        return [capability["intent"] for capability in self.registry.list_capabilities()]

    def _build_system_prompt(self) -> str:
        lines = [
            "Sei il pianificatore di Jake, un assistente personale locale.",
            "Scomponi la richiesta dell'utente in una sequenza ORDINATA di passi.",
            "Usa solo le capacita' elencate sotto, ciascuna con i suoi parametri.",
            "Se la richiesta e' gia' un singolo passo, restituisci comunque una lista con un elemento.",
            "Non inventare capacita' che non sono elencate.",
        ]
        context = self.context_provider() if self.context_provider else None
        if context:
            lines.append(
                f"Contesto (solo per capire a cosa si riferisce l'utente, non copiarlo mai nei "
                f"parametri se l'utente non lo dice esplicitamente): {context}"
            )
        lines.append("Capacita' disponibili:")
        for capability in self.registry.list_capabilities():
            lines.append(f"- {capability['intent']}: {capability['description']}")
            for name, meta in capability.get("parameters", {}).items():
                required = "obbligatorio" if meta.get("required") else "facoltativo"
                lines.append(f"    {name} ({meta.get('type', 'string')}, {required}): {meta.get('description', '')}")
        lines.append(
            "Rispondi solo con JSON valido secondo lo schema fornito: un oggetto con la chiave "
            "'steps', lista di oggetti {intent, parameters, description}. 'description' e' una "
            "frase breve in italiano che spiega cosa fa quel passo."
        )
        return "\n".join(lines)

    def _build_output_schema(self) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["steps"],
            "properties": {
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["intent", "parameters", "description"],
                        "properties": {
                            "intent": {"type": "string", "enum": self._valid_intents()},
                            "parameters": {"type": "object"},
                            "description": {"type": "string"},
                        },
                    },
                },
            },
        }

    def _request_ollama(self, text: str) -> dict:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "format": self._build_output_schema(),
            # Temperatura 0: i parametri (percorsi, nomi) vanno riprodotti esattamente,
            # non generati creativamente.
            "options": {"num_ctx": 8192, "temperature": 0},
            "messages": [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": text},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _parse_response(self, response: dict) -> dict:
        content = response["message"]["content"]
        payload = json.loads(content) if isinstance(content, str) else content
        if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
            raise ValueError("Planner output is not a valid steps object")
        return payload

    def _plan_from_payload(self, payload: dict) -> Plan:
        valid_intents = self._valid_intents()
        steps = []
        for raw_step in payload["steps"]:
            intent = raw_step.get("intent")
            parameters = raw_step.get("parameters")
            description = raw_step.get("description", "")
            if intent not in valid_intents or not isinstance(parameters, dict):
                raise ValueError("Invalid plan step")
            if self._has_unresolved_placeholder(parameters):
                # Il modello a volte "inventa" un riferimento al risultato di un passo
                # precedente (es. "{{ path_from_last_opened_project }}") che l'esecutore
                # non sa risolvere: meglio rifiutare l'intero piano che eseguirlo alla lettera.
                raise ValueError("Plan step references an unresolved placeholder")
            steps.append(PlanStep(intent=intent, parameters=parameters, description=description))
        if not steps:
            raise ValueError("Empty plan")
        return Plan(steps=steps)

    @staticmethod
    def _has_unresolved_placeholder(parameters: dict) -> bool:
        for value in parameters.values():
            if isinstance(value, str) and ("{{" in value or "}}" in value):
                return True
        return False
