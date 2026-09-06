from copy import deepcopy

from core.command import Command
from core.intent_provider import IntentProvider, OllamaProvider, RuleBasedProvider
from core.skill_registry import SkillRegistry


class Router:
    """Ordine di instradamento (v3.0):
    1. corsia veloce: la frase e' identica (a meno di maiuscole/punteggiatura) a un esempio
       noto o imparato -> nessuna chiamata al modello, risposta istantanea;
    2. classificatore Ollama con recupero semantico delle capacita' pertinenti;
    3. se Ollama non risponde: regole locali; se anche loro non capiscono, l'esempio piu'
       simile (solo con similarita' altissima e parametri presenti letteralmente nel testo)."""

    # Similarita' minima perche' un esempio "quasi identico" venga usato senza passare dal
    # modello: alta di proposito, meglio una chiamata in piu' che un'azione sbagliata.
    RETRIEVAL_SHORTCUT_SCORE = 0.93

    def __init__(
        self,
        primary_provider: IntentProvider = None,
        fallback_provider: IntentProvider = None,
        intent_provider: IntentProvider = None,
        skill_registry: SkillRegistry = None,
        example_store=None,
        retriever=None,
        client=None,
    ):
        self.skill_registry = skill_registry or SkillRegistry()
        config = getattr(self.skill_registry, "config", None)
        model = config.get("ollama_model", OllamaProvider.DEFAULT_MODEL) if config else OllamaProvider.DEFAULT_MODEL
        self.primary_provider = primary_provider or intent_provider or OllamaProvider(
            self.skill_registry, model=model, retriever=retriever, client=client,
        )
        if retriever is not None and hasattr(self.primary_provider, "retriever") and self.primary_provider.retriever is None:
            self.primary_provider.retriever = retriever
        self.fallback_provider = fallback_provider or RuleBasedProvider()
        self.intent_provider = self.primary_provider
        self.example_store = example_store
        self.retriever = retriever
        self.last_route = None  # "exact" | "llm" | "rules" | "retrieval" | None

    def detect_intent(self, text: str) -> Command:
        self.last_route = None

        if self.example_store is not None:
            example = self.example_store.find_exact(text)
            # Gli esempi dichiarati da una skill auto-generata (source "forge") non hanno i
            # parametri: servono solo al recupero semantico, mai alla corsia veloce.
            if example is not None and example.source != "forge":
                self.last_route = "exact"
                return Command(example.intent, deepcopy(example.parameters))

        command = self.primary_provider.detect_intent(text)
        if getattr(self.primary_provider, "last_error", None) is not None:
            self.last_route = "rules"
            fallback = self.fallback_provider.detect_intent(text)
            if fallback.intent != "UNKNOWN":
                return fallback
            shortcut = self._from_retrieval(text)
            return shortcut or fallback

        self.last_route = "llm"
        if command.intent == "UNKNOWN":
            shortcut = self._from_retrieval(text)
            if shortcut is not None:
                return shortcut
        return command

    def _from_retrieval(self, text: str) -> Command | None:
        if self.retriever is None:
            return None
        try:
            retrieval = self.retriever.retrieve(text, max_capabilities=5, max_examples=3)
        except Exception:
            return None
        example = retrieval.best_example
        if example is None or example.source == "forge" or retrieval.best_score < self.RETRIEVAL_SHORTCUT_SCORE:
            return None
        lowered = text.lower()
        for value in example.parameters.values():
            if isinstance(value, str) and value and value.lower() not in lowered:
                return None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and str(value) not in lowered:
                return None
        self.last_route = "retrieval"
        return Command(example.intent, deepcopy(example.parameters))
