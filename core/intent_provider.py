import re

from core.command import Command
from core.intent_provider_base import IntentProvider
# Il classificatore Ollama vive in core/nlu/llm_classifier.py dalla v3.0 (recupero semantico):
# riesportato qui per compatibilita' con chi importa OllamaProvider da questo modulo.
from core.nlu.llm_classifier import OllamaProvider  # noqa: E402,F401


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
		self.add_todo_triggers = ["aggiungi alla lista delle cose da fare", "metti in lista", "aggiungi un task"]
		self.list_todos_triggers = ["cosa devo fare", "mostrami la lista delle cose da fare", "elenca i task", "che task ho"]
		self.complete_todo_triggers = ["segna come fatto", "segna come completato", "ho completato il task", "completa il task"]
		self.shutdown_triggers = ["spegni il computer", "spegni il pc"]
		self.restart_triggers = ["riavvia il computer", "riavvia il pc"]
		self.sleep_triggers = ["metti in sospensione", "metti in standby", "sospendi il computer"]
		self.lock_triggers = ["blocca il computer", "blocca lo schermo"]
		self.brightness_triggers = ["luminosità al", "imposta la luminosità"]
		self.empty_recycle_bin_triggers = ["svuota il cestino"]
		self.list_wifi_triggers = ["reti wifi disponibili", "mostra le reti wifi", "cerca reti wifi"]
		self.git_status_triggers = ["stato git", "git status"]
		self.git_pull_triggers = ["git pull", "aggiorna il repository", "fai un pull"]
		self.open_in_editor_triggers = ["apri in vscode", "apri in visual studio code"]
		self.run_command_triggers = ["esegui il comando", "esegui questo comando", "lancia da terminale"]
		self.media_play_pause_triggers = ["metti in pausa la musica", "riprendi la musica", "play e pausa"]
		self.media_next_triggers = ["traccia successiva", "prossima canzone", "canzone successiva"]
		self.media_previous_triggers = ["traccia precedente", "canzone precedente"]
		self.tell_joke_triggers = ["raccontami una barzelletta", "dimmi una barzelletta", "fammi ridere"]
		self.roll_dice_triggers = ["tira un dado", "lancia un dado", "tira i dadi"]
		self.flip_coin_triggers = ["lancia una moneta", "testa o croce"]
		self.calculate_triggers = ["quanto fa", "quanto vale", "calcola"]
		self.convert_units_triggers = ["converti"]
		self.summarize_clipboard_triggers = [
			"riassumi gli appunti", "riassumi quello che ho copiato", "riassumi il contenuto degli appunti",
		]

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

	_MATH_WORDS_PATTERN = re.compile(r"\bpiu'?\b|\bmeno\b|\bper\b|\bdiviso\b|\bfratto\b")
	_MATH_WORDS = {"piu": "+", "piu'": "+", "meno": "-", "per": "*", "diviso": "/", "fratto": "/"}
	_CONVERT_PATTERN = re.compile(r"([\d.,]+)\s*([a-zàèéìòù]+)\s+in\s+([a-zàèéìòù]+)")

	@classmethod
	def _normalize_math_expression(cls, text: str) -> str:
		return cls._MATH_WORDS_PATTERN.sub(lambda m: cls._MATH_WORDS[m.group().rstrip("'")], text)

	def detect_intent(self, text: str) -> Command:
		if "apri" in text and "risultato" in text:
			index = self._extract_result_index(text)
			if index is not None:
				return Command("OPEN_SEARCH_RESULT", {"index": index})

		if any(trigger in text for trigger in self.open_url_triggers):
			url = self.extract_parameter_after_trigger(text, self.open_url_triggers)
			return Command("OPEN_URL", {"url": url})

		# Controllato prima di open_triggers: "apri in vscode" contiene anche "apri" e finirebbe
		# per essere interpretato come OPEN_APP se controllato dopo.
		if any(trigger in text for trigger in self.open_in_editor_triggers):
			path = self.extract_parameter_after_trigger(text, self.open_in_editor_triggers)
			return Command("OPEN_IN_EDITOR", {"path": path})

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

		if any(trigger in text for trigger in self.list_todos_triggers):
			return Command("LIST_TODOS", {})

		if any(trigger in text for trigger in self.complete_todo_triggers):
			content = self.extract_parameter_after_trigger(text, self.complete_todo_triggers)
			return Command("COMPLETE_TODO", {"text": content})

		if any(trigger in text for trigger in self.add_todo_triggers):
			content = self.extract_parameter_after_trigger(text, self.add_todo_triggers)
			return Command("ADD_TODO", {"text": content})

		if any(trigger in text for trigger in self.shutdown_triggers):
			return Command("SYSTEM_POWER", {"action": "shutdown"})
		if any(trigger in text for trigger in self.restart_triggers):
			return Command("SYSTEM_POWER", {"action": "restart"})
		if any(trigger in text for trigger in self.sleep_triggers):
			return Command("SYSTEM_POWER", {"action": "sleep"})
		if any(trigger in text for trigger in self.lock_triggers):
			return Command("SYSTEM_POWER", {"action": "lock"})

		if any(trigger in text for trigger in self.brightness_triggers):
			match = re.search(r"\d+", text)
			if match:
				return Command("SET_BRIGHTNESS", {"level": int(match.group())})

		if any(trigger in text for trigger in self.empty_recycle_bin_triggers):
			return Command("EMPTY_RECYCLE_BIN", {})

		if any(trigger in text for trigger in self.list_wifi_triggers):
			return Command("LIST_WIFI_NETWORKS", {})

		if any(trigger in text for trigger in self.git_status_triggers):
			path = self.extract_parameter_after_trigger(text, self.git_status_triggers)
			return Command("GIT_STATUS", {"path": path} if path else {})

		if any(trigger in text for trigger in self.git_pull_triggers):
			path = self.extract_parameter_after_trigger(text, self.git_pull_triggers)
			return Command("GIT_PULL", {"path": path} if path else {})

		if any(trigger in text for trigger in self.run_command_triggers):
			command = self.extract_parameter_after_trigger(text, self.run_command_triggers)
			return Command("RUN_COMMAND", {"command": command})

		if any(trigger in text for trigger in self.media_next_triggers):
			return Command("MEDIA_CONTROL", {"action": "next"})
		if any(trigger in text for trigger in self.media_previous_triggers):
			return Command("MEDIA_CONTROL", {"action": "previous"})
		if any(trigger in text for trigger in self.media_play_pause_triggers):
			return Command("MEDIA_CONTROL", {"action": "play_pause"})

		if any(trigger in text for trigger in self.tell_joke_triggers):
			return Command("TELL_JOKE", {})

		if any(trigger in text for trigger in self.flip_coin_triggers):
			return Command("FLIP_COIN", {})

		if any(trigger in text for trigger in self.roll_dice_triggers):
			return Command("ROLL_DICE", {})

		if any(trigger in text for trigger in self.summarize_clipboard_triggers):
			return Command("SUMMARIZE_CLIPBOARD", {})

		convert_match = self._CONVERT_PATTERN.search(text) if any(t in text for t in self.convert_units_triggers) else None
		if convert_match:
			return Command("CONVERT_UNITS", {
				"value": float(convert_match.group(1).replace(",", ".")),
				"from_unit": convert_match.group(2),
				"to_unit": convert_match.group(3),
			})

		if any(trigger in text for trigger in self.calculate_triggers):
			content = self.extract_parameter_after_trigger(text, self.calculate_triggers)
			return Command("CALCULATE", {"expression": self._normalize_math_expression(content)})

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
