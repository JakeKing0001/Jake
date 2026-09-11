"""Recupero delle capacita' pertinenti (v3.0).

Con ~200 capacita' il prompt "elenca tutto" supera i 10k token: il modello locale da 7B lo
vede troncato (contesto default di Ollama: 2048 token) o comunque si confonde, e risponde
UNKNOWN a quasi tutto. Qui, per ogni frase, si selezionano solo le ~15-20 capacita' piu'
plausibili (per similarita' con gli esempi di addestramento e con le descrizioni) e si
allegano gli esempi piu' simili come few-shot: prompt 5 volte piu' corto, risposte molto
piu' precise, e latenza piu' bassa."""
from dataclasses import dataclass, field
from pathlib import Path

from core.nlu.examples import Example, ExampleStore
from core.nlu.index import SemanticIndex

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "nlu_cache"

# Capacita' "di ripiego" sempre presenti nella lista candidata: sono quelle che catturano le
# richieste che non assomigliano a nessun comando specifico.
ALWAYS_INCLUDED = ("ASK_QUESTION", "CHITCHAT")


@dataclass
class Retrieval:
    capabilities: list[dict]
    examples: list[Example]
    best_example: Example | None = None
    best_score: float = 0.0
    intent_scores: dict = field(default_factory=dict)


class CapabilityRetriever:
    def __init__(self, registry, example_store: ExampleStore, embedder=None,
                 cache_dir: Path | None = None, model_name: str = "nomic-embed-text"):
        self.registry = registry
        self.example_store = example_store
        cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.example_index = SemanticIndex(embedder, cache_dir / "examples.json", model_name)
        self.capability_index = SemanticIndex(embedder, cache_dir / "capabilities.json", model_name)
        self._examples_by_key: dict[str, Example] = {}
        self._capabilities_by_intent: dict[str, dict] = {}

    # ---- costruzione -------------------------------------------------------------------

    @staticmethod
    def _capability_text(capability: dict) -> str:
        parts = [capability.get("intent", "").replace("_", " ").lower(), capability.get("description", "")]
        for name, meta in (capability.get("parameters") or {}).items():
            parts.append(f"{name}: {meta.get('description', '')}")
        return " ".join(part for part in parts if part)

    def refresh(self) -> None:
        """Ricostruisce entrambi gli indici (da chiamare all'avvio e quando si aggiungono skill)."""
        self._capabilities_by_intent = {
            capability["intent"]: capability for capability in self.registry.list_capabilities()
        }
        self.capability_index.build([
            (intent, self._capability_text(capability))
            for intent, capability in self._capabilities_by_intent.items()
        ])

        self._examples_by_key = {}
        items = []
        for example in self.example_store.all():
            # Gli esempi per intent che non esistono (piu') sono rumore: fuori.
            if example.intent not in self._capabilities_by_intent:
                continue
            self._examples_by_key[example.key] = example
            items.append((example.key, example.text))
        self.example_index.build(items)

    def add_example(self, example: Example) -> None:
        if example.intent not in self._capabilities_by_intent:
            return
        self._examples_by_key[example.key] = example
        self.example_index.add(example.key, example.text)

    def remove_example(self, text: str) -> None:
        from core.nlu.examples import normalize_key
        key = normalize_key(text)
        self._examples_by_key.pop(key, None)
        self.example_index.remove(key)

    def using_embeddings(self) -> bool:
        return self.example_index.using_embeddings()

    # ---- recupero ----------------------------------------------------------------------

    def retrieve(self, text: str, max_capabilities: int = 18, max_examples: int = 10) -> Retrieval:
        example_hits = self.example_index.search(text, k=30)
        capability_hits = self.capability_index.search(text, k=8)

        intent_scores: dict[str, float] = {}
        ordered_intents: list[str] = []
        best_example, best_score = None, 0.0
        for key, score in example_hits:
            example = self._examples_by_key.get(key)
            if example is None:
                continue
            if best_example is None:
                best_example, best_score = example, score
            if example.intent not in intent_scores:
                intent_scores[example.intent] = score
                ordered_intents.append(example.intent)
        for intent, score in capability_hits:
            if intent not in intent_scores:
                intent_scores[intent] = score * 0.9  # le descrizioni sono meno affidabili degli esempi
                ordered_intents.append(intent)

        # Ordina per punteggio ma tieni sempre i ripieghi generali.
        ordered_intents.sort(key=lambda intent: intent_scores.get(intent, 0.0), reverse=True)
        candidates = []
        for intent in ordered_intents:
            if intent in self._capabilities_by_intent and intent not in candidates:
                candidates.append(intent)
            if len(candidates) >= max_capabilities:
                break
        for intent in ALWAYS_INCLUDED:
            if intent in self._capabilities_by_intent and intent not in candidates:
                candidates.append(intent)

        candidate_set = set(candidates)
        examples: list[Example] = []
        per_intent: dict[str, int] = {}
        for key, _score in example_hits:
            example = self._examples_by_key.get(key)
            if example is None or example.intent not in candidate_set:
                continue
            if per_intent.get(example.intent, 0) >= 3:
                continue
            per_intent[example.intent] = per_intent.get(example.intent, 0) + 1
            examples.append(example)
            if len(examples) >= max_examples:
                break

        return Retrieval(
            capabilities=[self._capabilities_by_intent[intent] for intent in candidates],
            examples=examples,
            best_example=best_example,
            best_score=best_score,
            intent_scores=intent_scores,
        )
