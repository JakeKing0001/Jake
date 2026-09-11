"""Test unitari per core/nlu/retriever.py::CapabilityRetriever: nessuna suite esisteva finora,
nonostante sia il componente che seleziona le ~15-20 capacita' piu' plausibili passate al
classificatore di intent (senza questa potatura il prompt supererebbe i 10k token - vedi il
docstring del modulo). embedder=None per ogni SemanticIndex, cosi' la ricerca usa il fallback
lessicale deterministico (core.nlu.index.lexical_similarity), gia' testato altrove: qui si
verifica solo la logica di CapabilityRetriever stesso (selezione, deduplica, ripieghi sempre
inclusi, tetto di esempi per intent), non l'indice sottostante."""
import unittest

from core.nlu.examples import Example
from core.nlu.retriever import ALWAYS_INCLUDED, CapabilityRetriever


class FakeRegistry:
    def __init__(self, capabilities):
        self._capabilities = capabilities

    def list_capabilities(self):
        return self._capabilities


class FakeExampleStore:
    def __init__(self, examples):
        self._examples = examples

    def all(self):
        return self._examples


CAPABILITIES = [
    {"intent": "OPEN_APP", "description": "Apre un'applicazione."},
    {"intent": "GET_WEATHER", "description": "Restituisce il meteo per una citta'."},
    {"intent": "ASK_QUESTION", "description": "Risponde a una domanda di conoscenza generale."},
    {"intent": "CHITCHAT", "description": "Conversazione di cortesia."},
]

EXAMPLES = [
    Example(text="apri chrome", intent="OPEN_APP"),
    Example(text="apri il blocco note", intent="OPEN_APP"),
    Example(text="che tempo fa a roma", intent="GET_WEATHER"),
    Example(text="che tempo fa a milano", intent="GET_WEATHER"),
    Example(text="chi era napoleone", intent="ASK_QUESTION"),
]


def _retriever(capabilities=CAPABILITIES, examples=EXAMPLES):
    retriever = CapabilityRetriever(FakeRegistry(capabilities), FakeExampleStore(examples), embedder=None, cache_dir=None)
    retriever.refresh()
    return retriever


class RefreshTests(unittest.TestCase):
    def test_examples_for_unknown_intents_are_dropped(self):
        examples = list(EXAMPLES) + [Example(text="qualcosa d'altro", intent="INTENT_RIMOSSO")]
        retriever = _retriever(examples=examples)
        self.assertNotIn("qualcosa d'altro", retriever._examples_by_key)

    def test_using_embeddings_is_false_without_an_embedder(self):
        retriever = _retriever()
        self.assertFalse(retriever.using_embeddings())


class RetrieveTests(unittest.TestCase):
    def test_a_matching_query_surfaces_the_right_intent_and_example(self):
        retriever = _retriever()
        result = retriever.retrieve("apri il calcolatrice", max_capabilities=18, max_examples=10)
        intents = [c["intent"] for c in result.capabilities]
        self.assertIn("OPEN_APP", intents)
        self.assertIsNotNone(result.best_example)
        self.assertEqual(result.best_example.intent, "OPEN_APP")

    def test_always_included_fallbacks_are_present_even_without_a_match(self):
        retriever = _retriever()
        result = retriever.retrieve("xyz completamente estraneo qwerty")
        intents = [c["intent"] for c in result.capabilities]
        for fallback in ALWAYS_INCLUDED:
            self.assertIn(fallback, intents)

    def test_max_capabilities_is_respected_while_still_including_fallbacks(self):
        retriever = _retriever()
        result = retriever.retrieve("apri chrome", max_capabilities=1)
        # 1 candidato piu' selezione, piu' i ripieghi sempre inclusi che non erano gia' dentro.
        self.assertLessEqual(len(result.capabilities), 1 + len(ALWAYS_INCLUDED))
        intents = [c["intent"] for c in result.capabilities]
        for fallback in ALWAYS_INCLUDED:
            self.assertIn(fallback, intents)

    def test_examples_are_capped_at_three_per_intent(self):
        many_examples = [Example(text=f"apri l'app numero {i}", intent="OPEN_APP") for i in range(10)]
        retriever = _retriever(examples=many_examples)
        result = retriever.retrieve("apri l'app numero 3", max_examples=50)
        open_app_examples = [example for example in result.examples if example.intent == "OPEN_APP"]
        self.assertLessEqual(len(open_app_examples), 3)

    def test_max_examples_is_respected(self):
        retriever = _retriever()
        result = retriever.retrieve("apri chrome", max_examples=1)
        self.assertLessEqual(len(result.examples), 1)

    def test_no_examples_at_all_still_returns_fallback_capabilities(self):
        retriever = _retriever(examples=[])
        result = retriever.retrieve("qualsiasi cosa")
        self.assertIsNone(result.best_example)
        self.assertEqual(result.best_score, 0.0)
        intents = [c["intent"] for c in result.capabilities]
        for fallback in ALWAYS_INCLUDED:
            self.assertIn(fallback, intents)


class AddRemoveExampleTests(unittest.TestCase):
    def test_add_example_for_a_known_intent_makes_it_findable(self):
        retriever = _retriever(examples=[])
        retriever.add_example(Example(text="apri spotify", intent="OPEN_APP"))
        result = retriever.retrieve("apri spotify")
        self.assertIn("apri spotify", retriever._examples_by_key)
        self.assertEqual(result.best_example.text, "apri spotify")

    def test_add_example_for_an_unknown_intent_is_ignored(self):
        retriever = _retriever(examples=[])
        retriever.add_example(Example(text="qualcosa d'altro", intent="INTENT_INESISTENTE"))
        self.assertNotIn("qualcosa d'altro", retriever._examples_by_key)

    def test_remove_example_drops_it_from_future_retrievals(self):
        retriever = _retriever()
        retriever.remove_example("apri chrome")
        result = retriever.retrieve("apri chrome")
        texts = [example.text for example in result.examples]
        self.assertNotIn("apri chrome", texts)


if __name__ == "__main__":
    unittest.main()
