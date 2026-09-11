"""Apprendimento continuo (v3.0): Jake impara dai comandi che esegue e dalle correzioni.

Tre canali:
- insegnamento esplicito ("quando dico X fai Y") -> esempio con source 'taught';
- correzione ("no, intendevo Z") -> la frase precedente viene associata al comando giusto
  (source 'corrected') e, se differiva solo per una parola storpiata, quella parola entra
  nel vocabolario del normalizzatore (es. 'judy westcode' -> 'visual studio code');
- osservazione ("auto"): un comando risolto dal modello e andato a buon fine, che l'utente
  non ha corretto al turno successivo, diventa un esempio. La prossima volta la stessa frase
  prende la corsia veloce (nessuna chiamata al modello) e frasi simili classificano meglio.

Gli esempi automatici sono memorizzati solo se ogni parametro compare letteralmente nella
frase: cosi' non si cristallizzano valori allucinati dal modello."""
from copy import deepcopy

from core.command import Command

NON_LEARNABLE_INTENTS = {
    "UNKNOWN", "ASK_QUESTION", "CHITCHAT", "CORRECT_LAST", "REPEAT_LAST", "HELP",
    "STOP_TALKING", "PAUSE_LISTENING", "START_DICTATION", "STOP_DICTATION",
    "LEARN_COMMAND", "FORGET_LEARNED", "LIST_LEARNED",
    "CREATE_SKILL", "LIST_CREATED_SKILLS", "DELETE_CREATED_SKILL",
    "OPEN_SEARCH_RESULT", "RUN_COMMAND", "SET_MODEL", "SYSTEM_POWER", "DELETE_PATH",
}


class LearningManager:
    def __init__(self, example_store, retriever=None, normalizer=None, logger=None):
        self.example_store = example_store
        self.retriever = retriever
        self.normalizer = normalizer
        self.logger = logger
        self._pending = None  # (testo, intent, parametri) in attesa di conferma implicita

    # ---- osservazione ------------------------------------------------------------------

    @staticmethod
    def _parameters_grounded(text: str, parameters: dict | None) -> bool:
        lowered = (text or "").lower()
        for value in (parameters or {}).values():
            if isinstance(value, bool):
                continue
            if isinstance(value, str):
                if value and value.lower() not in lowered:
                    return False
            elif isinstance(value, (int, float)):
                if str(value) not in lowered and str(int(value)) not in lowered:
                    return False
            elif isinstance(value, list):
                if not all(isinstance(item, str) and item.lower() in lowered for item in value):
                    return False
            else:
                return False
        return True

    def observe(self, text: str, command: Command, result, route: str) -> None:
        """Da chiamare dopo ogni comando eseguito. Conferma implicitamente l'esempio in sospeso
        (l'utente e' andato avanti senza correggere) e valuta se il nuovo comando e' imparabile."""
        self.commit_pending()
        if route != "llm" or command is None or command.intent in NON_LEARNABLE_INTENTS:
            return
        if result is None or (not result.success and result.error != "CONFIRMATION_REQUIRED"):
            return
        if not self._parameters_grounded(text, command.parameters):
            return
        self._pending = (text, command.intent, deepcopy(command.parameters or {}))

    def commit_pending(self) -> None:
        if self._pending is None:
            return
        text, intent, parameters = self._pending
        self._pending = None
        existing = self.example_store.find_exact(text)
        if existing is not None and existing.source in ("taught", "corrected"):
            return
        self._store(text, intent, parameters, source="auto")

    def discard_pending(self) -> None:
        self._pending = None

    # ---- insegnamento e correzioni ------------------------------------------------------

    def _store(self, text: str, intent: str, parameters: dict, source: str):
        example = self.example_store.add_learned(text, intent, parameters, source=source)
        if self.retriever is not None:
            try:
                self.retriever.add_example(example)
            except Exception:
                if self.logger:
                    self.logger.exception("Errore aggiornando l'indice degli esempi")
        if self.logger:
            self.logger.info("Imparato (%s): %r -> %s %s", source, text, intent, parameters)
        return example

    def teach(self, phrase: str, intent: str, parameters: dict, source: str = "taught"):
        self.discard_pending()
        return self._store(phrase, intent, parameters or {}, source=source)

    def correct(self, previous_text: str, previous_command: Command | None, new_command: Command) -> None:
        self.discard_pending()
        if not previous_text or new_command is None or new_command.intent in NON_LEARNABLE_INTENTS:
            return
        self._store(previous_text, new_command.intent, new_command.parameters or {}, source="corrected")

        if self.normalizer is None or previous_command is None or previous_command.intent != new_command.intent:
            return
        differing = []
        for name, value in (new_command.parameters or {}).items():
            old_value = (previous_command.parameters or {}).get(name)
            if isinstance(value, str) and isinstance(old_value, str) and value.lower() != old_value.lower():
                differing.append((old_value, value))
        if len(differing) == 1:
            heard, meant = differing[0]
            self.normalizer.learn_replacement(heard, meant)
            if self.logger:
                self.logger.info("Vocabolario: %r -> %r", heard, meant)

    def forget(self, phrase: str) -> bool:
        removed = self.example_store.remove_learned(phrase)
        if removed and self.retriever is not None:
            self.retriever.remove_example(phrase)
        return removed

    def forget_intent(self, intent: str) -> int:
        removed = self.example_store.remove_learned_by_intent(intent)
        if removed and self.retriever is not None:
            try:
                self.retriever.refresh()
            except Exception:
                pass
        return removed

    def list_taught(self) -> list:
        return [example for example in self.example_store.learned() if example.source in ("taught", "corrected")]

    def count_auto(self) -> int:
        return sum(1 for example in self.example_store.learned() if example.source == "auto")
