"""Test unitari per la comprensione 'agentica' (v3.1): ripieghi, riferimenti recenti,
risoluzione dei pronomi. Nessuno di questi richiede Ollama o un vero registro di skill."""
import unittest

from core import fallbacks
from core.command import Command
from core.conversation_state import ConversationStateManager
from core.skill_result import SkillResult


class FakeAppResolver:
    """Simula AppResolver.resolve: 'spotify' e 'blender' sono app installate, il resto no."""

    def __init__(self, known: dict):
        self.known = known

    def resolve(self, name: str):
        name = name.strip().lower()
        if name in self.known:
            from dataclasses import dataclass

            @dataclass(frozen=True)
            class Match:
                requested: str
                matched_app: str
                score: float
                launcher: str

            return Match(requested=name, matched_app=self.known[name], score=0.95, launcher=f"{name}.exe")
        return None


class FakeSkill:
    def __init__(self, app_resolver=None):
        self.app_resolver = app_resolver


class FakeRegistry:
    """Registro minimale: OPEN_APP con un resolver finto, CLICK_ELEMENT e CLOSE_WINDOW presenti."""

    def __init__(self, known_apps: dict, has=("CLICK_ELEMENT", "CLOSE_WINDOW")):
        self._skills = {"OPEN_APP": FakeSkill(FakeAppResolver(known_apps))}
        for name in has:
            self._skills[name] = FakeSkill()

    def get_skill(self, intent):
        return self._skills.get(intent)

    def has_skill(self, intent):
        return intent in self._skills


class PreExecutionRewriteTests(unittest.TestCase):
    def test_rewrites_bare_app_name_to_open_app(self):
        registry = FakeRegistry({"spotify": "Spotify"})
        command = Command("OPEN_URL", {"url": "spotify"})
        rewritten = fallbacks.pre_execution_rewrite(command, registry)
        self.assertEqual(rewritten.intent, "OPEN_APP")
        self.assertEqual(rewritten.parameters["app"], "spotify")

    def test_leaves_real_domain_alone(self):
        registry = FakeRegistry({"spotify": "Spotify"})
        command = Command("OPEN_URL", {"url": "github.com"})
        rewritten = fallbacks.pre_execution_rewrite(command, registry)
        self.assertIs(rewritten, command)

    def test_leaves_unknown_bare_name_alone(self):
        registry = FakeRegistry({"spotify": "Spotify"})
        command = Command("OPEN_URL", {"url": "nonexistentapp"})
        rewritten = fallbacks.pre_execution_rewrite(command, registry)
        self.assertIs(rewritten, command)


class AlternativeForTests(unittest.TestCase):
    def test_open_app_unsupported_but_looks_like_known_site(self):
        registry = FakeRegistry({})
        command = Command("OPEN_APP", {"app": "youtube"})
        result = SkillResult(success=False, data={"app": "youtube"}, error="UNSUPPORTED_APP")
        alt, note = fallbacks.alternative_for(command, result, registry)
        self.assertEqual(alt.intent, "OPEN_URL")
        self.assertEqual(alt.parameters["url"], "youtube.com")

    def test_open_app_unsupported_and_not_a_site_returns_none(self):
        registry = FakeRegistry({})
        command = Command("OPEN_APP", {"app": "sfrizzlewhoop"})
        result = SkillResult(success=False, data={"app": "sfrizzlewhoop"}, error="UNSUPPORTED_APP")
        alt, note = fallbacks.alternative_for(command, result, registry)
        self.assertIsNone(alt)

    def test_focus_window_not_found_falls_back_to_open_app(self):
        registry = FakeRegistry({"blender": "Blender 4.3"})
        command = Command("FOCUS_WINDOW", {"title": "blender"})
        result = SkillResult(success=False, data={"title": "blender"}, error="WINDOW_NOT_FOUND")
        alt, note = fallbacks.alternative_for(command, result, registry)
        self.assertEqual(alt.intent, "OPEN_APP")
        self.assertEqual(alt.parameters["app"], "blender")
        self.assertIsNotNone(note)

    def test_click_text_not_found_falls_back_to_click_element(self):
        registry = FakeRegistry({})
        command = Command("CLICK_TEXT", {"text": "Accedi"})
        result = SkillResult(success=False, data={"text": "Accedi"}, error="NOT_FOUND")
        alt, note = fallbacks.alternative_for(command, result, registry)
        self.assertEqual(alt.intent, "CLICK_ELEMENT")
        self.assertEqual(alt.parameters["description"], "Accedi")

    def test_close_app_not_found_falls_back_to_close_window(self):
        registry = FakeRegistry({})
        command = Command("CLOSE_APP", {"name": "calcolatrice"})
        result = SkillResult(success=False, data={"name": "calcolatrice"}, error="NOT_FOUND")
        alt, note = fallbacks.alternative_for(command, result, registry)
        self.assertEqual(alt.intent, "CLOSE_WINDOW")

    def test_success_yields_no_alternative(self):
        registry = FakeRegistry({})
        command = Command("OPEN_APP", {"app": "spotify"})
        result = SkillResult(success=True, data={"app": "Spotify"})
        alt, note = fallbacks.alternative_for(command, result, registry)
        self.assertIsNone(alt)
        self.assertIsNone(note)


class OfferAfterFailureTests(unittest.TestCase):
    def test_offers_browser_search_for_missing_app(self):
        command = Command("OPEN_APP", {"app": "fotoritocco magico"})
        result = SkillResult(success=False, data={"app": "fotoritocco magico"}, error="UNSUPPORTED_APP")
        offer = fallbacks.offer_after_failure(command, result)
        self.assertEqual(offer["intent"], "SEARCH_IN_BROWSER")
        self.assertEqual(offer["parameters"]["query"], "fotoritocco magico")
        self.assertIn("fotoritocco magico", offer["message"])

    def test_no_offer_on_success(self):
        command = Command("OPEN_APP", {"app": "spotify"})
        result = SkillResult(success=True, data={"app": "Spotify"})
        self.assertIsNone(fallbacks.offer_after_failure(command, result))


class ConversationEntitiesTests(unittest.TestCase):
    def test_remembers_and_summarizes_entities(self):
        state = ConversationStateManager()
        state.remember_entities("OPEN_APP", {"app": "spotify"}, {"app": "Spotify"})
        entities = state.get_entities()
        self.assertEqual(entities["app"], "Spotify")
        self.assertIn("Spotify", state.entities_summary())

    def test_find_file_remembers_first_result_as_path(self):
        state = ConversationStateManager()
        state.remember_entities(
            "FIND_FILE", {"name": "tesi.pdf"},
            {"name": "tesi.pdf", "results": ["C:\\Users\\david\\Documents\\tesi.pdf", "C:\\other\\tesi.pdf"]},
        )
        self.assertEqual(state.get_entities()["path"], "C:\\Users\\david\\Documents\\tesi.pdf")

    def test_close_window_remembers_title_as_app(self):
        state = ConversationStateManager()
        state.remember_entities("FOCUS_WINDOW", {"title": "blender"}, {"title": "Blender 4.3"})
        self.assertEqual(state.get_entities()["app"], "Blender 4.3")


class PronounResolutionTests(unittest.TestCase):
    """Usa direttamente i pattern statici di JakeCore, senza costruire l'intero core (che
    richiederebbe Ollama/registro completo)."""

    @classmethod
    def setUpClass(cls):
        from core.jake_core import JakeCore
        cls.JakeCore = JakeCore

    def _resolve(self, text: str, entities: dict) -> str:
        core = self.JakeCore.__new__(self.JakeCore)  # bypassa __init__ (nessuna dipendenza pesante)
        core.conversation_state = type("S", (), {"get_entities": lambda self: entities})()
        return self.JakeCore._resolve_pronouns(core, text)

    def test_aprilo_uses_last_path(self):
        self.assertEqual(self._resolve("aprilo", {"path": "C:\\Users\\david\\Documents\\tesi.pdf"}), "apri C:\\Users\\david\\Documents\\tesi.pdf")

    def test_apri_quello_uses_last_app_if_no_path(self):
        self.assertEqual(self._resolve("apri quello", {"app": "Spotify"}), "apri Spotify")

    def test_chiudilo_uses_last_app(self):
        self.assertEqual(self._resolve("chiudilo", {"app": "Blender 4.3", "path": "C:\\x\\y.pdf"}), "chiudi Blender 4.3")

    def test_leggilo_uses_last_path(self):
        self.assertEqual(self._resolve("leggilo", {"path": "C:\\note.txt"}), "leggi il file C:\\note.txt")

    def test_no_entity_leaves_text_untouched(self):
        self.assertEqual(self._resolve("aprilo", {}), "aprilo")
        self.assertEqual(self._resolve("aprilo", {"query": "solo una ricerca"}), "aprilo")

    def test_does_not_fire_inside_longer_sentence(self):
        # "quello" dentro una frase normale non deve attivare la sostituzione pronominale.
        self.assertEqual(self._resolve("cerca ricette con quello che ho in frigo", {"path": "C:\\x.pdf"}), "cerca ricette con quello che ho in frigo")


if __name__ == "__main__":
    unittest.main()
