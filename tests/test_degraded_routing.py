"""Instradamento a GPU ceduta (core/nlu/degraded_routing.py, Router.degraded): prima le corsie senza modello, poi il
classificatore compatto. Nessun Ollama: provider e resolver finti."""
import types
import unittest
import unittest.mock

from core.command import Command
from core.intent_provider import RuleBasedProvider
from core.nlu.degraded_routing import is_knowledge_question, rule_fast_path
from core.router import Router


class _Resolver:
    def resolve(self, app):
        known = {"spotify": "Spotify", "discord": "Discord"}
        name = known.get(app.lower())
        return types.SimpleNamespace(matched_app=name, score=1.0) if name else None


class _Classifier:
    supports_compact = True

    def __init__(self, result=None, error=None):
        self.result, self.error = result or Command("UNKNOWN", {}), error
        self.calls = []
        self.last_error = None

    def detect_intent(self, text, compact=False, model=None):
        self.calls.append((text, compact, model))
        self.last_error = self.error
        return self.result


class _Registry:
    config = None

    def get_skill(self, name):
        return types.SimpleNamespace(app_resolver=_Resolver()) if name == "OPEN_APP" else None


def _router(classifier, degraded=True, model=None):
    router = Router(primary_provider=classifier, skill_registry=_Registry())
    router.degraded = lambda: degraded
    router.degraded_model = lambda: model
    return router


class FastPathTest(unittest.TestCase):
    def setUp(self):
        self.rules = RuleBasedProvider()

    def test_safe_commands_skip_the_model(self):
        self.assertEqual(rule_fast_path("mi apri spotify per favore", self.rules, _Resolver()).parameters,
                         {"app": "spotify"})
        self.assertEqual(rule_fast_path("quanta batteria mi resta", self.rules).intent, "GET_BATTERY_STATUS")
        self.assertEqual(rule_fast_path("abbassa un po' il volume", self.rules).parameters, {"action": "down"})
        self.assertEqual(rule_fast_path("che ore sono adesso", self.rules).intent, "GET_TIME")

    def test_unsafe_or_ambiguous_commands_go_to_the_model(self):
        for text in ("a che ora parte il treno per milano",   # acchiappa-tutto "ora" delle regole
                     "chiudi chrome", "elimina la cartella temp",   # distruttivi
                     "che ore sono e che giorno e' oggi",           # composta
                     "no intendevo apri spotify",                   # correzione
                     "apri amazon"):                                # non e' un'app installata
            self.assertIsNone(rule_fast_path(text, self.rules, _Resolver()), text)

    def test_knowledge_questions(self):
        self.assertTrue(is_knowledge_question("spiegami cos'è un buco nero"))
        self.assertTrue(is_knowledge_question("chi era Giuseppe Garibaldi"))
        self.assertFalse(is_knowledge_question("perché il mio pc è lento"))
        self.assertFalse(is_knowledge_question("perché me l'hai detto"))
        self.assertFalse(is_knowledge_question("quanta RAM sto usando"))


class DegradedRouterTest(unittest.TestCase):
    def test_fast_lanes_never_call_the_model(self):
        classifier = _Classifier()
        router = _router(classifier)
        self.assertEqual(router.detect_intent("alza il volume").intent, "SET_VOLUME")
        self.assertEqual(router.last_route, "fast-rules")
        command = router.detect_intent("spiegami cos'è un buco nero")
        self.assertEqual((command.intent, router.last_route), ("ASK_QUESTION", "fast-knowledge"))
        self.assertEqual(command.parameters, {"question": "spiegami cos'è un buco nero"})
        self.assertEqual(classifier.calls, [])

    def test_rest_goes_to_compact_classifier_on_the_chosen_model(self):
        classifier = _Classifier(Command("CLOSE_APP", {"name": "chrome"}))
        router = _router(classifier, model="light:1b")
        self.assertEqual(router.detect_intent("chiudi chrome").intent, "CLOSE_APP")
        self.assertEqual(classifier.calls, [("chiudi chrome", True, "light:1b")])
        self.assertEqual(router.last_route, "llm")

    def test_model_failure_still_falls_back_to_rules(self):
        classifier = _Classifier(error="OLLAMA_UNAVAILABLE")
        router = _router(classifier)
        self.assertEqual(router.detect_intent("chiudi chrome").intent, "CLOSE_APP")
        self.assertEqual(router.last_route, "rules")

    def test_normal_mode_is_unchanged(self):
        classifier = _Classifier(Command("SET_VOLUME", {"action": "up"}))
        router = _router(classifier, degraded=False)
        router.detect_intent("alza il volume")
        self.assertEqual(classifier.calls, [("alza il volume", False, None)])


class YieldOptionsTest(unittest.TestCase):
    def test_non_primary_models_stay_off_the_gpu_while_yielding(self):
        import core.ollama_client as client

        policy = types.SimpleNamespace(yielding=True)
        with unittest.mock.patch.object(client, "_gpu_policy", policy), \
                unittest.mock.patch.object(client, "_settings", lambda: {"primary": "main:7b"}):
            self.assertEqual(client.runtime_options("light:1b", {"temperature": 0})["num_gpu"], 0)
            policy.yielding = False
            self.assertNotIn("num_gpu", client.runtime_options("light:1b", {"temperature": 0}))


if __name__ == "__main__":
    unittest.main()
