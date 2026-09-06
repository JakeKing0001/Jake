"""Classificatore di intent basato su Ollama con recupero semantico (v3.0).

Rispetto alla versione precedente:
- il prompt elenca solo le capacita' pertinenti alla frase (CapabilityRetriever), non tutte
  le ~200: prompt 5 volte piu' corto, meno confusione, meno latenza;
- allega esempi few-shot simili alla frase, presi dal dataset e da cio' che Jake ha imparato;
- imposta num_ctx esplicito: il default di Ollama (2048 token) troncava il prompt in silenzio;
- usa il client condiviso (127.0.0.1, keep_alive lungo) invece di urllib diretto."""
import json
from copy import deepcopy

from core.command import Command
from core.intent_provider_base import IntentProvider
from core.ollama_client import OllamaClient, OllamaResponseError, OllamaUnavailable


class OllamaProvider(IntentProvider):
    # Nessun modello e' bundlato con Ollama: quello configurato deve esistere gia' scaricato
    # (`ollama pull <modello>`), altrimenti ogni richiesta fallisce e si passa al fallback.
    DEFAULT_MODEL = "qwen2.5:7b"
    UNKNOWN_INTENT = "UNKNOWN"
    NUM_CTX = 8192
    MAX_OUTPUT_TOKENS = 300

    def __init__(
        self,
        registry,
        base_url: str = None,
        timeout: float = 25,
        model: str = None,
        context_provider=None,
        retriever=None,
        client: OllamaClient = None,
    ):
        self.registry = registry
        self.client = client or OllamaClient(base_url=base_url, timeout=timeout)
        self.base_url = self.client.base_url
        self.timeout = timeout
        self.model = model or self.DEFAULT_MODEL
        self.last_error = None
        # Contestualizzazione desktop (v2.0): callable opzionale che restituisce una riga di
        # contesto (es. finestre usate di recente), per risolvere richieste ambigue.
        self.context_provider = context_provider
        # Recupero semantico (v3.0): se assente, il prompt elenca tutte le capacita' (come prima).
        self.retriever = retriever
        self.last_retrieval = None
        self.last_prompt_chars = 0

    # ---- selezione capacita' -------------------------------------------------------------

    def _select(self, text: str):
        all_capabilities = self.registry.list_capabilities()
        self.last_retrieval = None
        if self.retriever is None:
            return all_capabilities, []
        try:
            retrieval = self.retriever.retrieve(text)
        except Exception:
            return all_capabilities, []
        self.last_retrieval = retrieval
        return (retrieval.capabilities or all_capabilities), retrieval.examples

    # ---- API pubblica ------------------------------------------------------------------

    def detect_intent(self, text: str) -> Command:
        self.last_error = None
        try:
            capabilities, examples = self._select(text)
            response = self._request_ollama(text, capabilities, examples)
            payload = self._parse_response(response)
            return self._command_from_payload(payload, capabilities)
        except OllamaUnavailable:
            self.last_error = "OLLAMA_UNAVAILABLE"
        except (OllamaResponseError, TimeoutError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self.last_error = "INVALID_OLLAMA_RESPONSE"
        except Exception:
            self.last_error = "OLLAMA_ERROR"
        return Command(self.UNKNOWN_INTENT, {})

    def get_valid_intents(self, capabilities: list = None) -> list[str]:
        capabilities = capabilities if capabilities is not None else self.registry.list_capabilities()
        intents = [capability["intent"] for capability in capabilities]
        if self.UNKNOWN_INTENT not in intents:
            intents.append(self.UNKNOWN_INTENT)
        return intents

    def build_output_schema(self, capabilities: list = None) -> dict:
        capabilities = capabilities if capabilities is not None else self.registry.list_capabilities()
        parameter_properties = {}
        for capability in capabilities:
            for name, metadata in capability.get("parameters", {}).items():
                parameter_schema = deepcopy(metadata)
                parameter_schema.pop("required", None)
                parameter_schema.pop("description", None)
                parameter_properties.setdefault(name, parameter_schema)

        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent", "parameters"],
            "properties": {
                "intent": {"type": "string", "enum": self.get_valid_intents(capabilities)},
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": parameter_properties,
                },
            },
        }

    def build_system_prompt(self, capabilities: list = None, examples: list = None) -> str:
        capabilities = capabilities if capabilities is not None else self.registry.list_capabilities()
        lines = [
            "Sei il parser degli intent di Jake, un assistente vocale italiano che controlla un PC Windows.",
            "Il testo arriva dal riconoscimento vocale: puo' contenere piccoli errori di trascrizione, "
            "interpreta il senso piu' probabile.",
            "Rispondi esclusivamente con JSON valido secondo lo schema fornito: {\"intent\": ..., \"parameters\": {...}}.",
        ]
        context = self.context_provider() if self.context_provider else None
        if context:
            lines.append(
                f"Contesto (solo per capire a cosa si riferisce l'utente in richieste ambigue, "
                f"es. pronomi o 'quel file': non copiarlo mai nei parametri se l'utente non lo "
                f"dice esplicitamente): {context}"
            )
        lines.append("Capacita' disponibili (scegline UNA):")
        for capability in capabilities:
            lines.append(f"- {capability['intent']}: {capability['description']}")
            parameters = capability.get("parameters", {})
            if not parameters:
                lines.append("  Parametri: nessuno.")
                continue
            for name, metadata in parameters.items():
                required = "obbligatorio" if metadata.get("required") else "facoltativo"
                lines.append(
                    f"  - {name} ({metadata.get('type', 'string')}, {required}): {metadata.get('description', '')}"
                )
        if examples:
            lines.append("Esempi di richieste gia' interpretate correttamente (segui lo stesso stile):")
            for example in examples:
                if example.source == "forge":
                    # Esempio dichiarato da una skill auto-generata: solo l'intent e' noto.
                    lines.append(f'- "{example.text}" -> {json.dumps({"intent": example.intent}, ensure_ascii=False)} (parametri da estrarre dalla frase)')
                    continue
                lines.append(
                    f'- "{example.text}" -> '
                    + json.dumps({"intent": example.intent, "parameters": example.parameters}, ensure_ascii=False)
                )
        lines.extend([
            "Regole:",
            "- Usa UNKNOWN se nessuna capacita' e' appropriata o se manca un valore obbligatorio che "
            "l'utente non ha detto: non inventare MAI valori (percorsi, nomi, citta', testo).",
            "- Copia i valori letterali cosi' come li dice l'utente (percorsi anche 'parlati' come "
            "'desktop\\\\note.txt' o 'download'), senza correggerli o tradurli.",
            "- Tempi: 'tra 10 minuti' -> in_minutes=10, 'tra 2 ore' -> in_minutes=120, 'alle 9' -> at_time=\"09:00\", "
            "'alle 18:30' -> at_time=\"18:30\".",
            "- Se la richiesta descrive piu' azioni distinte in sequenza (es. 'e poi', 'quindi') e "
            "nessuna capacita' la copre tutta, usa UNKNOWN: non eseguirne solo una parte. Eccezione: "
            "le capacita' con un parametro pensato per una descrizione libera (es. SAVE_WORKFLOW, "
            "LEARN_COMMAND), dove l'intera richiesta va in quel parametro.",
            "- Saluti, ringraziamenti, commenti senza una richiesta: CHITCHAT. Domande di conoscenza "
            "generale o richieste di aiuto testuale: ASK_QUESTION.",
            "- Non inventare intent o parametri e non aggiungere spiegazioni o Markdown.",
        ])
        prompt = "\n".join(lines)
        self.last_prompt_chars = len(prompt)
        return prompt

    # ---- dettagli ----------------------------------------------------------------------

    def _request_ollama(self, text: str, capabilities: list, examples: list) -> dict:
        return self.client.chat(
            self.model,
            messages=[
                {"role": "system", "content": self.build_system_prompt(capabilities, examples)},
                {"role": "user", "content": text},
            ],
            format=self.build_output_schema(capabilities),
            # Temperatura 0: qui serve riprodurre esattamente percorsi e testo letterale
            # dell'utente, non generare variazioni creative.
            options={"temperature": 0, "num_ctx": self.NUM_CTX, "num_predict": self.MAX_OUTPUT_TOKENS},
            timeout=self.timeout,
        )

    def _parse_response(self, response: dict) -> dict:
        content = response["message"]["content"]
        payload = json.loads(content) if isinstance(content, str) else content
        if not isinstance(payload, dict):
            raise ValueError("Ollama output is not an object")
        return payload

    def _command_from_payload(self, payload: dict, capabilities: list) -> Command:
        intent = payload.get("intent")
        parameters = payload.get("parameters")
        if not isinstance(parameters, dict) or intent not in self.get_valid_intents(capabilities):
            raise ValueError("Invalid intent contract")

        if intent == self.UNKNOWN_INTENT:
            return Command(intent, {})

        capability = next(capability for capability in capabilities if capability["intent"] == intent)
        metadata = capability.get("parameters", {})
        validated_parameters = {}
        for name, value in parameters.items():
            if name not in metadata:
                continue  # parametro di un'altra capacita' (schema condiviso): ignorato
            if value is None:
                continue
            if value == "" and not metadata[name].get("required"):
                continue
            if not self._matches_type(value, metadata[name].get("type")):
                value = self._coerce(value, metadata[name].get("type"))
                if value is None:
                    raise ValueError("Invalid parameter type")
            validated_parameters[name] = value
        for name, parameter in metadata.items():
            if parameter.get("required") and name not in validated_parameters:
                raise ValueError("Missing required parameter")

        if "app" in validated_parameters and isinstance(validated_parameters["app"], str):
            validated_parameters["app"] = validated_parameters["app"].strip()
        return Command(intent, validated_parameters)

    @staticmethod
    def _coerce(value, type_name: str):
        """Il modello a volte scrive '10' invece di 10: tollera le conversioni ovvie."""
        try:
            if type_name == "integer" and isinstance(value, (str, float)):
                return int(float(str(value).strip()))
            if type_name == "number" and isinstance(value, str):
                return float(value.strip().replace(",", "."))
            if type_name == "string" and isinstance(value, (int, float)):
                return str(value)
            if type_name == "boolean" and isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in ("true", "si", "sì", "yes", "1", "on"):
                    return True
                if lowered in ("false", "no", "0", "off"):
                    return False
            if type_name == "array" and isinstance(value, str):
                return [part.strip() for part in value.split(",") if part.strip()]
        except (ValueError, TypeError):
            return None
        return None

    @staticmethod
    def _matches_type(value, type_name: str) -> bool:
        if type_name == "string":
            return isinstance(value, str)
        if type_name == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if type_name == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if type_name == "boolean":
            return isinstance(value, bool)
        if type_name == "array":
            return isinstance(value, list)
        if type_name == "object":
            return isinstance(value, dict)
        return False
