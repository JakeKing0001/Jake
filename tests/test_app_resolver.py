"""Test unitari per core/app_resolver.py: nessuna suite esisteva finora. resolve() e' isolato
dal filesystem/PowerShell reali costruendo AppResolver con search_paths=[]/
include_start_apps=False (niente scansione vera del menu Start o Get-StartApps) e iniettando
direttamente _applications/_display_names/_sources per i test sul fuzzy matching - le uniche
parti che davvero toccano il sistema (_add_start_menu_entries/_add_start_apps/_add_path_entries)
restano fuori scopo qui, sono I/O non deterministico.

F1 (indiretto): buco reale trovato e corretto in questa sessione. resolve() gestiva gia'
l'articolo elidibile senza apostrofo ("il pannello di controllo" -> "pannello di controllo"),
ma MAI quello con apostrofo ("l'esplora file"): normalize_name() toglie la punteggiatura PRIMA
che il controllo sull'articolo veda il testo, quindi "l'esplora file" diventava "lesplora file"
(l'apostrofo sparisce senza lasciare uno spazio) e il controllo storico
"normalized_name.startswith('l ')" non scattava mai per questo caso - "apri l'esplora file", una
frase italiana perfettamente naturale, non risolveva affatto l'alias."""
import unittest

from core.app_resolver import AppMatch, AppResolver


def _resolver(**kwargs) -> AppResolver:
    return AppResolver(search_paths=[], include_start_apps=False, **kwargs)


class NormalizeNameTests(unittest.TestCase):
    def test_lowercases_and_collapses_whitespace(self):
        self.assertEqual(AppResolver.normalize_name("  Visual   Studio Code  "), "visual studio code")

    def test_strips_known_launcher_suffixes(self):
        for suffix in (".lnk", ".exe", ".bat", ".cmd"):
            self.assertEqual(AppResolver.normalize_name(f"notepad{suffix}"), "notepad")

    def test_strips_directory_components_and_keeps_only_the_stem(self):
        self.assertEqual(AppResolver.normalize_name(r"C:\Program Files\App\app.exe"), "app")

    def test_replaces_underscores_and_hyphens_with_spaces(self):
        self.assertEqual(AppResolver.normalize_name("visual_studio-code"), "visual studio code")

    def test_strips_punctuation(self):
        self.assertEqual(AppResolver.normalize_name("assassin's creed"), "assassins creed")


class KnownAliasResolutionTests(unittest.TestCase):
    def test_resolves_a_known_alias_directly(self):
        match = _resolver().resolve("blocco note")
        self.assertEqual(match.launcher, "notepad.exe")
        self.assertEqual(match.score, 1.0)

    def test_alias_lookup_is_case_and_space_insensitive(self):
        match = _resolver().resolve("  BLOCCO   NOTE  ")
        self.assertEqual(match.launcher, "notepad.exe")

    def test_returns_none_for_an_empty_or_whitespace_name(self):
        self.assertIsNone(_resolver().resolve(""))
        self.assertIsNone(_resolver().resolve("   "))

    def test_leading_article_without_elision_is_stripped(self):
        match = _resolver().resolve("il pannello di controllo")
        self.assertEqual(match.launcher, "control.exe")


class ElidedArticleRegressionTests(unittest.TestCase):
    """Il buco reale trovato e corretto in questa sessione."""

    def test_apostrophe_elided_article_is_stripped_with_a_straight_quote(self):
        match = _resolver().resolve("l'esplora file")
        self.assertIsNotNone(match)
        self.assertEqual(match.launcher, "explorer.exe")

    def test_apostrophe_elided_article_is_stripped_with_a_curly_quote(self):
        match = _resolver().resolve("l\u2019esplora file")
        self.assertIsNotNone(match)
        self.assertEqual(match.launcher, "explorer.exe")

    def test_elision_does_not_misfire_on_a_word_that_merely_starts_with_l(self):
        self.assertIsNone(_resolver().resolve("lavagna"))

    def test_elision_works_for_a_settings_style_alias_too(self):
        match = _resolver().resolve("l'impostazioni")
        self.assertEqual(match.launcher, "ms-settings:")


class FuzzyMatchingAgainstDiscoveredAppsTests(unittest.TestCase):
    def _resolver_with_apps(self, applications: dict, sources: dict = None, display_names: dict = None) -> AppResolver:
        resolver = _resolver()
        resolver._applications = dict(applications)
        resolver._sources = dict(sources or {name: "start_menu" for name in applications})
        resolver._display_names = dict(display_names or {name: name for name in applications})
        return resolver

    def test_exact_match_against_a_discovered_app(self):
        resolver = self._resolver_with_apps({"spotify": "spotify.exe"})
        match = resolver.resolve("spotify")
        self.assertEqual(match.launcher, "spotify.exe")
        self.assertEqual(match.score, 1.0)

    def test_close_typo_still_resolves_above_the_default_threshold(self):
        resolver = self._resolver_with_apps({"spotify": "spotify.exe"})
        match = resolver.resolve("spotfy")
        self.assertIsNotNone(match)
        self.assertEqual(match.launcher, "spotify.exe")

    def test_unrelated_name_does_not_resolve(self):
        resolver = self._resolver_with_apps({"spotify": "spotify.exe"})
        self.assertIsNone(resolver.resolve("qualcosa di completamente diverso"))

    def test_path_sourced_entries_require_a_much_higher_score(self):
        """PATH_MIN_SCORE (0.92) e' piu' alto della soglia normale (0.72): un nome dal PATH
        (rumoroso, pieno di piccoli eseguibili) deve essere quasi identico per contare."""
        resolver = self._resolver_with_apps({"curl": "curl.exe"}, sources={"curl": "path"})
        self.assertIsNone(resolver.resolve("curl tool"))  # somiglianza troppo bassa per il PATH
        match = resolver.resolve("curl")
        self.assertEqual(match.launcher, "curl.exe")

    def test_abbreviation_of_a_multi_word_app_name_resolves(self):
        """'vscode' deve risolvere 'visual studio code' (iniziali delle prime parole + ultima
        parola per intero): la stessa logica usata per abbreviazioni comuni."""
        resolver = self._resolver_with_apps({"visual studio code": "code.exe"})
        match = resolver.resolve("vscode")
        self.assertIsNotNone(match)
        self.assertEqual(match.launcher, "code.exe")

    def test_word_prefix_of_a_multi_word_app_name_resolves(self):
        resolver = self._resolver_with_apps({"visual studio code": "code.exe"})
        match = resolver.resolve("visual studio")
        self.assertIsNotNone(match)
        self.assertEqual(match.launcher, "code.exe")

    def test_display_name_is_used_in_the_match_when_available(self):
        resolver = self._resolver_with_apps(
            {"spotify": "spotify.exe"}, display_names={"spotify": "Spotify Music"},
        )
        match = resolver.resolve("spotify")
        self.assertEqual(match.matched_app, "Spotify Music")

    def test_returns_an_appmatch_instance(self):
        resolver = self._resolver_with_apps({"spotify": "spotify.exe"})
        self.assertIsInstance(resolver.resolve("spotify"), AppMatch)


if __name__ == "__main__":
    unittest.main()
