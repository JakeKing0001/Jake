"""Test unitari della comprensione (v3.0): non richiedono Ollama, microfono o GUI.
Esecuzione: .venv\\Scripts\\python.exe -m unittest discover -s tests -v"""
import json
import tempfile
import unittest
from pathlib import Path

from core.command import Command
from core.nlu import chitchat
from core.nlu.examples import ExampleStore, normalize_key
from core.nlu.index import SemanticIndex, lexical_similarity
from core.nlu.normalizer import TranscriptNormalizer, _compound_number
from core.path_resolver import resolve_user_path
from core.router import Router
from core.skill_result import SkillResult


class NormalizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.normalizer = TranscriptNormalizer(
            app_names_provider=lambda: ["Blender 4.3", "Visual Studio Code", "Opera", "Spotify", "Discord"],
            learned_vocabulary_path=Path(self.tmp.name) / "vocab.json",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_punctuation_and_case(self):
        self.assertEqual(self.normalizer.normalize("Apri Blender!"), "apri blender")
        self.assertEqual(self.normalizer.normalize("  Che ore sono?  "), "che ore sono")

    def test_wake_word_prefix_removed(self):
        self.assertEqual(self.normalizer.normalize("Jake, apri Spotify."), "apri spotify")

    def test_whisper_fixes(self):
        self.assertEqual(self.normalizer.normalize("A nulla questo timer"), "annulla questo timer")
        self.assertEqual(self.normalizer.normalize("post on timer di trenta minuti"), "imposta un timer di 30 minuti")
        self.assertEqual(self.normalizer.normalize("udi visual studio code"), "apri visual studio code")
        self.assertEqual(self.normalizer.normalize("apri judy westcode"), "apri visual studio code")

    def test_numbers(self):
        self.assertEqual(_compound_number("venticinque"), 25)
        self.assertEqual(_compound_number("ventuno"), 21)
        self.assertEqual(_compound_number("trentotto"), 38)
        self.assertEqual(_compound_number("centocinquanta"), 150)
        self.assertEqual(_compound_number("duecento"), 200)
        self.assertEqual(self.normalizer.normalize("metti un timer di venticinque minuti"), "metti un timer di 25 minuti")
        self.assertEqual(self.normalizer.normalize("tra sei minuti"), "tra 6 minuti")
        self.assertEqual(self.normalizer.normalize("sei sveglio?"), "sei sveglio")
        self.assertEqual(self.normalizer.normalize("grazie mille"), "grazie mille")
        self.assertEqual(self.normalizer.normalize("volume al quaranta per cento"), "volume al 40 per cento")

    def test_times(self):
        self.assertEqual(self.normalizer.normalize("ricordami alle sette e mezza di uscire"), "ricordami alle 7:30 di uscire")
        self.assertEqual(self.normalizer.normalize("alle 18 e 30"), "alle 18:30")
        self.assertEqual(self.normalizer.normalize("tra un quarto d'ora"), "tra 15 minuti")
        self.assertEqual(self.normalizer.normalize("timer di un'ora e mezza"), "timer di 90 minuti")

    def test_app_name_fuzzy_fix(self):
        self.assertEqual(self.normalizer.normalize("apri blendr"), "apri blender")
        self.assertEqual(self.normalizer.normalize("apri discor"), "apri discord")
        # troppo diverso: non toccare
        self.assertEqual(self.normalizer.normalize("apri qualcosa"), "apri qualcosa")

    def test_learned_replacement_persists(self):
        self.normalizer.learn_replacement("giudi westcod", "visual studio code")
        self.assertEqual(self.normalizer.normalize("apri giudi westcod"), "apri visual studio code")
        reloaded = TranscriptNormalizer(learned_vocabulary_path=Path(self.tmp.name) / "vocab.json")
        self.assertEqual(reloaded.normalize("apri giudi westcod"), "apri visual studio code")


class ChitchatTests(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(chitchat.classify("grazie mille"), "thanks")
        self.assertEqual(chitchat.classify("ciao jake"), "greeting")
        self.assertEqual(chitchat.classify("come stai"), "howareyou")
        self.assertEqual(chitchat.classify("chi sei"), "whoareyou")
        self.assertIsNone(chitchat.classify("apri spotify"))

    def test_reply_not_none_for_small_talk(self):
        self.assertTrue(chitchat.reply("grazie"))
        self.assertIsNone(chitchat.reply("metti un timer"))


class ExampleStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        builtin = Path(self.tmp.name) / "intents.jsonl"
        builtin.write_text(
            json.dumps({"text": "che ore sono", "intent": "GET_TIME", "parameters": {}}) + "\n"
            + json.dumps({"text": "apri spotify", "intent": "OPEN_APP", "parameters": {"app": "spotify"}}) + "\n",
            encoding="utf-8",
        )
        self.learned = Path(self.tmp.name) / "learned.jsonl"
        self.store = ExampleStore(builtin_path=builtin, learned_path=self.learned)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_lookup_is_normalized(self):
        self.assertEqual(self.store.find_exact("Che ore sono?").intent, "GET_TIME")
        self.assertIsNone(self.store.find_exact("che ora è"))

    def test_learned_overrides_builtin_and_persists(self):
        self.store.add_learned("apri spotify", "PLAY_MEDIA", {"query": "spotify"}, source="corrected")
        self.assertEqual(self.store.find_exact("apri spotify").intent, "PLAY_MEDIA")
        reloaded = ExampleStore(builtin_path=self.store.builtin_path, learned_path=self.learned)
        self.assertEqual(reloaded.find_exact("apri spotify").source, "corrected")
        self.assertTrue(reloaded.remove_learned("apri spotify"))
        self.assertEqual(reloaded.find_exact("apri spotify").intent, "OPEN_APP")

    def test_normalize_key(self):
        self.assertEqual(normalize_key("  Ciao!!  "), "ciao")


class SemanticIndexTests(unittest.TestCase):
    def test_lexical_fallback_when_no_embedder(self):
        index = SemanticIndex(embedder=None)
        index.build([("a", "apri spotify"), ("b", "che ore sono"), ("c", "metti un timer di 5 minuti")])
        self.assertFalse(index.using_embeddings())
        best = index.search("che ora è", k=1)[0][0]
        self.assertEqual(best, "b")
        self.assertGreater(lexical_similarity("apri spotify", "apri spotify"), 0.99)

    def test_embeddings_with_fake_embedder(self):
        vectors = {"apri spotify": [1.0, 0.0], "che ore sono": [0.0, 1.0], "che ora è": [0.1, 0.9]}

        def embed(texts):
            return [vectors.get(text, [0.5, 0.5]) for text in texts]

        with tempfile.TemporaryDirectory() as tmp:
            index = SemanticIndex(embedder=embed, cache_path=Path(tmp) / "cache.json", model_name="fake")
            index.build([("a", "apri spotify"), ("b", "che ore sono")])
            self.assertTrue(index.using_embeddings())
            self.assertEqual(index.search("che ora è", k=1)[0][0], "b")
            self.assertTrue((Path(tmp) / "cache.json").is_file())


class FakeProvider:
    def __init__(self, intent="UNKNOWN", parameters=None, error=None):
        self.intent = intent
        self.parameters = parameters or {}
        self.last_error = error
        self.retriever = None
        self.calls = 0

    def detect_intent(self, text):
        self.calls += 1
        return Command(self.intent, dict(self.parameters))


class FakeRegistry:
    config = None
    skills = {}

    def list_capabilities(self):
        return []


class RouterTests(unittest.TestCase):
    def test_exact_example_skips_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            builtin = Path(tmp) / "intents.jsonl"
            builtin.write_text(json.dumps({"text": "che ore sono", "intent": "GET_TIME", "parameters": {}}) + "\n", encoding="utf-8")
            store = ExampleStore(builtin_path=builtin, learned_path=Path(tmp) / "learned.jsonl")
            primary = FakeProvider(intent="OPEN_APP", parameters={"app": "x"})
            router = Router(primary_provider=primary, fallback_provider=FakeProvider(), skill_registry=FakeRegistry(), example_store=store)
            command = router.detect_intent("Che ore sono?")
            self.assertEqual(command.intent, "GET_TIME")
            self.assertEqual(router.last_route, "exact")
            self.assertEqual(primary.calls, 0)
            command = router.detect_intent("apri spotify")
            self.assertEqual(command.intent, "OPEN_APP")
            self.assertEqual(router.last_route, "llm")

    def test_fallback_to_rules_when_llm_fails(self):
        primary = FakeProvider(intent="UNKNOWN", error="OLLAMA_UNAVAILABLE")
        fallback = FakeProvider(intent="GET_TIME")
        router = Router(primary_provider=primary, fallback_provider=fallback, skill_registry=FakeRegistry())
        self.assertEqual(router.detect_intent("che ore sono").intent, "GET_TIME")
        self.assertEqual(router.last_route, "rules")


class PathResolverTests(unittest.TestCase):
    def test_known_folders(self):
        desktop = resolve_user_path("desktop")
        self.assertTrue(desktop.lower().endswith("desktop"))
        self.assertTrue(resolve_user_path("download").lower().endswith("downloads"))
        self.assertTrue(resolve_user_path("desktop\\note.txt").lower().endswith("desktop\\note.txt"))
        self.assertTrue(resolve_user_path("la cartella documenti").lower().endswith("documents"))

    def test_absolute_and_special(self):
        self.assertEqual(resolve_user_path("C:\\Windows"), "C:\\Windows")
        self.assertEqual(resolve_user_path("cestino"), "shell:RecycleBinFolder")


class ResponseFormatterTests(unittest.TestCase):
    def test_new_intents(self):
        from core.response_formatter import format_skill_result

        self.assertIn("Timer di 2 minuti", format_skill_result("SET_TIMER", SkillResult(True, {"duration": "2 minuti", "label": ""})))
        self.assertEqual(format_skill_result("CLICK_TEXT", SkillResult(False, {"text": "Accedi"}, error="NOT_FOUND")), "Non trovo 'Accedi' sullo schermo.")
        self.assertEqual(format_skill_result("CHITCHAT", SkillResult(True, {"reply": "Prego."})), "Prego.")
        self.assertEqual(format_skill_result("SCROLL", SkillResult(True, {"direction": "down", "amount": 8})), "")


if __name__ == "__main__":
    unittest.main()
