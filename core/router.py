from core.intent_provider import OllamaProvider, IntentProvider, RuleBasedProvider
from core.skill_registry import SkillRegistry

class Router:
    def __init__(
        self,
        primary_provider: IntentProvider = None,
        fallback_provider: IntentProvider = None,
        intent_provider: IntentProvider = None,
        skill_registry: SkillRegistry = None,
    ):
        self.skill_registry = skill_registry or SkillRegistry()
        config = getattr(self.skill_registry, "config", None)
        model = config.get("ollama_model", OllamaProvider.DEFAULT_MODEL) if config else OllamaProvider.DEFAULT_MODEL
        self.primary_provider = primary_provider or intent_provider or OllamaProvider(self.skill_registry, model=model)
        self.fallback_provider = fallback_provider or RuleBasedProvider()
        self.intent_provider = self.primary_provider

    def detect_intent(self, text: str):
        command = self.primary_provider.detect_intent(text)
        if getattr(self.primary_provider, "last_error", None) is not None:
            return self.fallback_provider.detect_intent(text)
        return command