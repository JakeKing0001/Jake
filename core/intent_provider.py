from abc import ABC, abstractmethod
from copy import deepcopy
import json
import re
from urllib import error, request

from core.command import Command


class IntentProvider(ABC):
	"""Interfaccia per i componenti che trasformano testo in Command."""

	@abstractmethod
	def detect_intent(self, text: str) -> Command:
		"""Riconosce l'intent e restituisce il comando corrispondente."""
		raise NotImplementedError


class RuleBasedProvider(IntentProvider):
	"""Riconosce gli intent usando regole e keyword locali."""

	ORDINALS = {"primo": 1, "prima": 1, "secondo": 2, "terzo": 3, "quarto": 4, "quinto": 5}

	def __init__(self):
		self.open_triggers = ["apri", "aprimi", "avvia", "avviami"]
		self.remember_triggers = ["ricorda che", "ricordati che", "memorizza che"]
		self.forget_triggers = ["dimentica", "scorda"]
		self.recall_triggers = ["cosa ricordi su", "cosa sai su", "ricordi", "recupera", "richiama"]
		self.create_folder_triggers = ["crea la cartella", "crea una cartella", "crea cartella"]
		self.create_file_triggers = ["crea il file", "crea un file", "crea file"]
		self.delete_triggers = ["elimina", "cancella"]
		self.find_file_triggers = ["trova il file", "trova file", "cerca il file"]
		self.search_files_triggers = [
			"cerca nel contenuto", "cerca con nest", "cerca nei documenti", "cerca nei file",
		]
		self.open_url_triggers = ["apri il sito", "vai su", "apri url"]
		self.weather_triggers = ["che tempo fa a", "meteo a", "che tempo fa", "meteo"]
		self.news_triggers = ["notizie su", "news su", "ultime notizie"]
		self.web_search_triggers = ["cerca sul web", "cerca su internet", "cerca online"]
		self.clipboard_read_triggers = ["cosa c'e' negli appunti", "leggi gli appunti", "cosa ho copiato"]
		self.volume_up_triggers = ["alza il volume", "aumenta il volume"]
		self.volume_down_triggers = ["abbassa il volume", "diminuisci il volume"]
		self.volume_mute_triggers = ["silenzia", "muta l'audio", "togli l'audio"]
		self.list_processes_triggers = ["mostra i processi", "elenca i processi", "che processi sono aperti"]
		self.close_app_triggers = ["chiudi", "termina"]
		self.reminder_triggers = ["ricordami di", "ricordami che", "promemoria"]
		self.list_reminders_triggers = ["che promemoria ho", "quali promemoria ho", "i miei promemoria"]
		self.screenshot_triggers = ["fai uno screenshot", "cattura lo schermo", "scatta uno screenshot"]
		self.read_screen_triggers = ["leggi lo schermo", "cosa c'e' scritto sullo schermo", "leggi cosa c'e' sullo schermo"]
		self.active_window_triggers = ["cosa sto facendo", "che finestra ho aperto", "cosa ho aperto ora"]
		self.semantic_search_triggers = ["cerca per significato", "trova per significato", "cerca concettualmente"]
		self.build_semantic_index_triggers = ["prepara l'indice semantico", "aggiorna l'indice semantico"]
		self.research_triggers = ["fai una ricerca approfondita su", "ricerca approfondita su", "informati su"]

	def extract_parameter_after_trigger(self, text: str, triggers: list) -> str:
		"""Estrae il parametro dopo un trigger, ripulendo gli spazi."""
		for trigger in triggers:
			if trigger in text:
				idx = text.find(trigger)
				return text[idx + len(trigger):].strip()
		return ""

	@staticmethod
	def _split_key_value(content: str) -> tuple[str, str]:
		"""Divide 'chiave è valore' in (chiave, valore); senza separatore usa il testo per entrambi."""
		for separator in [" è ", " e' ", ": "]:
			if separator in content:
				key, value = content.split(separator, 1)
				return key.strip(), value.strip()
		return content.strip(), content.strip()

	@classmethod
	def _extract_result_index(cls, text: str) -> int | None:
		"""Estrae un numero o un ordinale italiano (primo, secondo, ...) dal testo."""
		match = re.search(r"\d+", text)
		if match:
			return int(match.group())
		for word, value in cls.ORDINALS.items():
			if word in text:
				return value
		return None

	@staticmethod
	def _parse_reminder(content: str) -> dict | None:
		"""Estrae 'tra N minuti/ore' o 'alle HH:MM' dal testo dopo il trigger, ripulendo il testo
		del promemoria dalla parte temporale. None se non trova nessun riferimento all'orario."""
		minutes_match = re.search(r"\btra\s+(\d+)\s*(minut\w*|or[ae]\w*)\b", content)
		if minutes_match:
			amount = int(minutes_match.group(1))
			unit = minutes_match.group(2)
			in_minutes = amount * 60 if unit.startswith("or") else amount
			reminder_text = (content[:minutes_match.start()] + content[minutes_match.end():]).strip(" ,.")
			return {"text": reminder_text or content.strip(), "in_minutes": in_minutes}

		time_match = re.search(r"\balle\s+(\d{1,2})[:.](\d{2})\b", content)
		if time_match:
			at_time = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
			reminder_text = (content[:time_match.start()] + content[time_match.end():]).strip(" ,.")
			return {"text": reminder_text or content.strip(), "at_time": at_time}

		return None

	def detect_intent(self, text: str) -> Command:
		if "apri" in text and "risultato" in text:
			index = self._extract_result_index(text)
			if index is not None:
				return Command("OPEN_SEARCH_RESULT", {"index": index})

		if any(trigger in text for trigger in self.open_url_triggers):
			url = self.extract_parameter_after_trigger(text, self.open_url_triggers)
			return Command("OPEN_URL", {"url": url})

		if any(trigger in text for trigger in self.open_triggers):
			app_name = self.extract_parameter_after_trigger(text, self.open_triggers)
			return Command("OPEN_APP", {"app": app_name})

		if any(trigger in text for trigger in self.remember_triggers):
			content = self.extract_parameter_after_trigger(text, self.remember_triggers)
			key, value = self._split_key_value(content)
			return Command("REMEMBER", {"key": key, "value": value})

		if any(trigger in text for trigger in self.forget_triggers):
			key = self.extract_parameter_after_trigger(text, self.forget_triggers)
			return Command("FORGET", {"key": key})

		if any(trigger in text for trigger in self.recall_triggers):
			query = self.extract_parameter_after_trigger(text, self.recall_triggers)
			return Command("RECALL", {"query": query})

		if any(trigger in text for trigger in self.create_folder_triggers):
			path = self.extract_parameter_after_trigger(text, self.create_folder_triggers)
			return Command("CREATE_PATH", {"path": path, "type": "folder"})

		if any(trigger in text for trigger in self.create_file_triggers):
			path = self.extract_parameter_after_trigger(text, self.create_file_triggers)
			return Command("CREATE_PATH", {"path": path, "type": "file"})

		if any(trigger in text for trigger in self.delete_triggers):
			path = self.extract_parameter_after_trigger(text, self.delete_triggers)
			return Command("DELETE_PATH", {"path": path})

		if any(trigger in text for trigger in self.search_files_triggers):
			query = self.extract_parameter_after_trigger(text, self.search_files_triggers)
			return Command("SEARCH_FILES", {"query": query})

		if any(trigger in text for trigger in self.find_file_triggers):
			name = self.extract_parameter_after_trigger(text, self.find_file_triggers)
			return Command("FIND_FILE", {"name": name})

		if any(trigger in text for trigger in self.semantic_search_triggers):
			query = self.extract_parameter_after_trigger(text, self.semantic_search_triggers)
			return Command("SEMANTIC_SEARCH_FILES", {"query": query})

		if any(trigger in text for trigger in self.build_semantic_index_triggers):
			return Command("BUILD_SEMANTIC_INDEX", {})

		if any(trigger in text for trigger in self.research_triggers):
			topic = self.extract_parameter_after_trigger(text, self.research_triggers)
			return Command("RESEARCH", {"topic": topic})

		if any(trigger in text for trigger in self.weather_triggers):
			city = self.extract_parameter_after_trigger(text, self.weather_triggers)
			return Command("GET_WEATHER", {"city": city})

		if any(trigger in text for trigger in self.news_triggers):
			topic = self.extract_parameter_after_trigger(text, self.news_triggers)
			return Command("GET_NEWS", {"topic": topic})

		if any(trigger in text for trigger in self.web_search_triggers):
			query = self.extract_parameter_after_trigger(text, self.web_search_triggers)
			return Command("WEB_SEARCH", {"query": query})

		if any(trigger in text for trigger in self.clipboard_read_triggers):
			return Command("CLIPBOARD_READ", {})

		if any(trigger in text for trigger in self.volume_up_triggers):
			return Command("SET_VOLUME", {"action": "up"})
		if any(trigger in text for trigger in self.volume_down_triggers):
			return Command("SET_VOLUME", {"action": "down"})
		if any(trigger in text for trigger in self.volume_mute_triggers):
			return Command("SET_VOLUME", {"action": "mute"})

		if any(trigger in text for trigger in self.list_processes_triggers):
			return Command("LIST_PROCESSES", {})

		if any(trigger in text for trigger in self.close_app_triggers):
			name = self.extract_parameter_after_trigger(text, self.close_app_triggers)
			return Command("CLOSE_APP", {"name": name})

		if any(trigger in text for trigger in self.list_reminders_triggers):
			return Command("LIST_REMINDERS", {})

		if any(trigger in text for trigger in self.reminder_triggers):
			content = self.extract_parameter_after_trigger(text, self.reminder_triggers)
			parsed = self._parse_reminder(content)
			if parsed is not None:
				return Command("SET_REMINDER", parsed)

		if any(trigger in text for trigger in self.screenshot_triggers):
			return Command("TAKE_SCREENSHOT", {})

		if any(trigger in text for trigger in self.read_screen_triggers):
			return Command("READ_SCREEN", {})

		if any(trigger in text for trigger in self.active_window_triggers):
			return Command("GET_ACTIVE_WINDOW", {})

		if re.search(r"\b(ora|ore)\b", text):
			return Command("GET_TIME")
		if re.search(r"\b(data|giorno|oggi)\b", text):
			return Command("GET_DATE")
		return Command("UNKNOWN")


class OllamaProvider(IntentProvider):
	# Nessun modello e' bundlato con Ollama: quello configurato deve esistere già scaricato
	# (`ollama pull <modello>`), altrimenti ogni richiesta fallisce con 404 e si passa al
	# fallback rule-based senza errori visibili. Configurabile via "ollama_model" in config.
	DEFAULT_MODEL = "qwen2.5:7b"
	UNKNOWN_INTENT = "UNKNOWN"

	def __init__(
		self,
		registry,
		base_url: str = "http://localhost:11434",
		timeout: float = 25,
		model: str = None,
		context_provider=None,
	):
		self.registry = registry
		self.base_url = base_url.rstrip("/")
		self.timeout = timeout
		self.model = model or self.DEFAULT_MODEL
		self.last_error = None
		# Contestualizzazione desktop (v2.0): callable opzionale che restituisce una riga di
		# contesto (es. finestre usate di recente), per risolvere richieste ambigue.
		self.context_provider = context_provider

	def detect_intent(self, text: str) -> Command:
		self.last_error = None
		try:
			response = self._request_ollama(text)
			payload = self._parse_response(response)
			return self._command_from_payload(payload)
		except error.URLError:
			self.last_error = "OLLAMA_UNAVAILABLE"
		except (TimeoutError, ValueError, TypeError, KeyError, json.JSONDecodeError):
			self.last_error = "INVALID_OLLAMA_RESPONSE"
		except Exception:
			self.last_error = "OLLAMA_ERROR"
		return Command("UNKNOWN", {})

	def get_valid_intents(self) -> list[str]:
		intents = [capability["intent"] for capability in self.registry.list_capabilities()]
		if self.UNKNOWN_INTENT not in intents:
			intents.append(self.UNKNOWN_INTENT)
		return intents

	def build_output_schema(self) -> dict:
		parameter_properties = {}
		for capability in self.registry.list_capabilities():
			for name, metadata in capability.get("parameters", {}).items():
				parameter_schema = deepcopy(metadata)
				parameter_schema.pop("required", None)
				parameter_properties.setdefault(name, parameter_schema)

		return {
			"type": "object",
			"additionalProperties": False,
			"required": ["intent", "parameters"],
			"properties": {
				"intent": {
					"type": "string",
					"enum": self.get_valid_intents(),
				},
				"parameters": {
					"type": "object",
					"additionalProperties": False,
					"properties": parameter_properties,
				},
			},
		}

	def build_system_prompt(self) -> str:
		lines = [
			"Sei il parser degli intent di Jake.",
			"Rispondi esclusivamente con JSON valido secondo lo schema fornito.",
		]
		context = self.context_provider() if self.context_provider else None
		if context:
			lines.append(
				f"Contesto (solo per capire a cosa si riferisce l'utente in richieste ambigue, "
				f"es. pronomi o 'quel file': non copiarlo mai nei parametri se l'utente non lo "
				f"dice esplicitamente): {context}"
			)
		lines.append("Capacita disponibili:")
		for capability in self.registry.list_capabilities():
			lines.append(f"- {capability['intent']}: {capability['description']}")
			parameters = capability.get("parameters", {})
			if not parameters:
				lines.append("  Parametri: nessuno.")
				continue
			for name, metadata in parameters.items():
				required = "obbligatorio" if metadata.get("required") else "facoltativo"
				lines.append(
					f"  Parametro {name}: {metadata.get('type', 'string')}, "
					f"{required}. {metadata.get('description', '')}"
				)
		lines.extend([
				"Usa UNKNOWN se nessuna capacita e appropriata.",
				"Usa UNKNOWN anche se la richiesta e' generica o di alto livello (es. 'prepara l'ambiente "
				"di lavoro') e non specifica esplicitamente i valori letterali richiesti (percorsi, nomi, "
				"citta', testo). Non inventare MAI un valore per un parametro obbligatorio: se il valore "
				"non compare nel messaggio dell'utente, usa UNKNOWN invece di indovinarlo.",
				"Se la richiesta descrive piu' azioni distinte da eseguire in sequenza (es. contiene "
				"'e poi', 'quindi') e nessuna singola capacita' tra quelle elencate puo' rappresentare "
				"l'intera richiesta in una sola volta, usa UNKNOWN: non eseguire solo una parte della "
				"richiesta scartando il resto in silenzio. Fanno eccezione le capacita' che hanno "
				"esplicitamente un parametro pensato per contenere una descrizione libera e articolata "
				"(es. SAVE_WORKFLOW): in quel caso l'intera richiesta va dentro quel parametro.",
				"Non inventare intent o parametri e non aggiungere spiegazioni o Markdown.",
		])
		return "\n".join(lines)

	def _request_ollama(self, text: str) -> dict:
		payload = {
			"model": self.model,
			"stream": False,
			"format": self.build_output_schema(),
			# Temperatura 0: qui serve riprodurre esattamente percorsi e testo letterale
			# dell'utente, non generare variazioni creative (che corrompono i backslash
			# nei percorsi Windows o "correggono" maiuscole/minuscole nei nomi).
			"options": {"temperature": 0},
			"messages": [
				{"role": "system", "content": self.build_system_prompt()},
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
		if not isinstance(payload, dict):
			raise ValueError("Ollama output is not an object")
		return payload

	def _command_from_payload(self, payload: dict) -> Command:
		intent = payload.get("intent")
		parameters = payload.get("parameters")
		if not isinstance(parameters, dict) or intent not in self.get_valid_intents():
			raise ValueError("Invalid intent contract")

		if intent == self.UNKNOWN_INTENT:
			if parameters:
				raise ValueError("UNKNOWN cannot have parameters")
			return Command(intent, {})

		capability = next(
			capability for capability in self.registry.list_capabilities()
			if capability["intent"] == intent
		)
		metadata = capability.get("parameters", {})
		if any(name not in metadata for name in parameters):
			raise ValueError("Unexpected parameters")
		for name, parameter in metadata.items():
			if parameter.get("required") and name not in parameters:
				raise ValueError("Missing required parameter")
			if name in parameters and not self._matches_type(parameters[name], parameter.get("type")):
				raise ValueError("Invalid parameter type")

		validated_parameters = deepcopy(parameters)
		if "app" in validated_parameters:
			validated_parameters["app"] = validated_parameters["app"].strip()
		return Command(intent, validated_parameters)

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