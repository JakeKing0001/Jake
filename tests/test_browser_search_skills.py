"""Test unitari per skills/browser_search.py: nessuna suite esisteva finora. webbrowser.open/
os.startfile/urllib.request.urlopen sono sempre mockati: un test che li chiamasse per davvero
aprirebbe pagine web vere durante la suite."""
import unittest
from unittest import mock

from skills.browser_search import PlayMediaSkill, SearchInBrowserSkill, normalize_site


class NormalizeSiteTests(unittest.TestCase):
    def test_known_site_passes_through(self):
        self.assertEqual(normalize_site("youtube"), "youtube")

    def test_unknown_site_falls_back_to_google(self):
        self.assertEqual(normalize_site("un sito mai sentito"), "google")

    def test_empty_site_falls_back_to_google(self):
        self.assertEqual(normalize_site(""), "google")
        self.assertEqual(normalize_site(None), "google")

    def test_leading_italian_preposition_is_stripped(self):
        self.assertEqual(normalize_site("su youtube"), "youtube")
        self.assertEqual(normalize_site("sulla amazon"), "amazon")

    def test_elided_apostrophe_form_is_not_recognized(self):
        """Limite noto, non corretto qui: il regex delle preposizioni riconosce 'sulla ' (con
        spazio) ma non la forma elisa 'sull'' (apostrofo, grammaticalmente corretta prima di
        una vocale come 'amazon') - ricade su google invece che sul sito inteso. Impatto basso:
        'site' e' un parametro gia' estratto dal modello, non testo grezzo trascritto, quindi
        arriva quasi sempre gia' come 'amazon' senza la preposizione."""
        self.assertEqual(normalize_site("sull'amazon"), "google")

    def test_aliases_are_resolved(self):
        self.assertEqual(normalize_site("yt"), "youtube")
        self.assertEqual(normalize_site("wiki"), "wikipedia")
        self.assertEqual(normalize_site("twitter"), "x")

    def test_browser_names_are_treated_as_a_generic_google_search(self):
        for name in ("chrome", "edge", "firefox", "opera", "browser"):
            self.assertEqual(normalize_site(name), "google")


class SearchInBrowserTests(unittest.TestCase):
    def test_missing_query_opens_the_site_homepage(self):
        with mock.patch("webbrowser.open", return_value=True) as browser_open:
            result = SearchInBrowserSkill().execute({"query": "", "site": "youtube"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["url"], "https://www.youtube.com")
        browser_open.assert_called_once_with("https://www.youtube.com")

    def test_a_query_opens_the_search_results_page(self):
        with mock.patch("webbrowser.open", return_value=True) as browser_open:
            result = SearchInBrowserSkill().execute({"query": "gatti", "site": "youtube"})
        self.assertTrue(result.success)
        self.assertIn("gatti", result.data["url"])
        browser_open.assert_called_once()

    def test_default_site_is_google(self):
        with mock.patch("webbrowser.open", return_value=True):
            result = SearchInBrowserSkill().execute({"query": "python"})
        self.assertEqual(result.data["site"], "google")

    def test_maps_query_uses_plain_quoting_not_plus_encoding(self):
        with mock.patch("webbrowser.open", return_value=True):
            result = SearchInBrowserSkill().execute({"query": "via roma 1", "site": "maps"})
        self.assertNotIn("+", result.data["url"])

    def test_browser_open_failure_is_reported(self):
        with mock.patch("webbrowser.open", return_value=False):
            result = SearchInBrowserSkill().execute({"query": "python"})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_browser_open_exception_is_reported_not_raised(self):
        with mock.patch("webbrowser.open", side_effect=Exception("boom")):
            result = SearchInBrowserSkill().execute({"query": "python"})
        self.assertEqual(result.error, "OPERATION_FAILED")


class PlayMediaRoutingTests(unittest.TestCase):
    def test_missing_query_fails(self):
        result = PlayMediaSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_video_keyword_routes_to_youtube_by_default(self):
        with mock.patch.object(PlayMediaSkill, "_play_youtube", return_value="youtube-result") as play_youtube:
            result = PlayMediaSkill().execute({"query": "un video divertente"})
        self.assertEqual(result, "youtube-result")
        play_youtube.assert_called_once_with("un video divertente")

    def test_plain_query_routes_to_spotify_by_default(self):
        with mock.patch.object(PlayMediaSkill, "_play_spotify", return_value="spotify-result") as play_spotify:
            result = PlayMediaSkill().execute({"query": "una canzone qualsiasi"})
        self.assertEqual(result, "spotify-result")
        play_spotify.assert_called_once_with("una canzone qualsiasi")

    def test_explicit_service_overrides_the_keyword_heuristic(self):
        with mock.patch.object(PlayMediaSkill, "_play_spotify", return_value="spotify-result") as play_spotify:
            PlayMediaSkill().execute({"query": "un video divertente", "service": "spotify"})
        play_spotify.assert_called_once()


class PlaySpotifyTests(unittest.TestCase):
    def test_startfile_success_does_not_open_a_browser(self):
        with mock.patch("os.startfile") as startfile:
            with mock.patch("webbrowser.open") as browser_open:
                result = PlayMediaSkill()._play_spotify("una canzone")
        self.assertTrue(result.success)
        self.assertEqual(result.data["service"], "spotify")
        startfile.assert_called_once()
        browser_open.assert_not_called()

    def test_startfile_failure_falls_back_to_the_browser(self):
        with mock.patch("os.startfile", side_effect=OSError("app non trovata")):
            with mock.patch("webbrowser.open", return_value=True) as browser_open:
                result = PlayMediaSkill()._play_spotify("una canzone")
        self.assertTrue(result.success)
        browser_open.assert_called_once()

    def test_both_startfile_and_browser_failing_is_reported(self):
        with mock.patch("os.startfile", side_effect=OSError("boom")):
            with mock.patch("webbrowser.open", side_effect=Exception("boom")):
                result = PlayMediaSkill()._play_spotify("una canzone")
        self.assertEqual(result.error, "OPERATION_FAILED")


class PlayYoutubeTests(unittest.TestCase):
    def test_offline_reports_network_unavailable(self):
        with mock.patch("skills.browser_search.is_online", return_value=False):
            result = PlayMediaSkill()._play_youtube("una canzone")
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_a_found_video_id_opens_the_direct_watch_url_with_autoplay(self):
        with mock.patch("skills.browser_search.is_online", return_value=True):
            with mock.patch.object(PlayMediaSkill, "_first_youtube_video", return_value=("abc12345678", "Un video")):
                with mock.patch("webbrowser.open", return_value=True) as browser_open:
                    result = PlayMediaSkill()._play_youtube("una canzone")
        self.assertTrue(result.success)
        self.assertTrue(result.data["autoplay"])
        self.assertIn("abc12345678", result.data["url"])
        browser_open.assert_called_once_with("https://www.youtube.com/watch?v=abc12345678")

    def test_no_video_found_falls_back_to_the_results_page_without_autoplay(self):
        with mock.patch("skills.browser_search.is_online", return_value=True):
            with mock.patch.object(PlayMediaSkill, "_first_youtube_video", return_value=(None, None)):
                with mock.patch("webbrowser.open", return_value=True):
                    result = PlayMediaSkill()._play_youtube("una canzone")
        self.assertTrue(result.success)
        self.assertFalse(result.data["autoplay"])


class FirstYoutubeVideoParsingTests(unittest.TestCase):
    """Logica pura di estrazione (regex sull'HTML grezzo), nessuna vera richiesta di rete."""

    def _with_html(self, html: str):
        response = mock.MagicMock()
        response.read.return_value = html.encode("utf-8")
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return mock.patch("urllib.request.urlopen", return_value=response)

    def test_extracts_the_video_id_and_title(self):
        html = '"videoRenderer":{"videoId":"dQw4w9WgXcQ","thumbnail":{},"title":{"runs":[{"text":"Titolo del video"}]}'
        with self._with_html(html):
            video_id, title = PlayMediaSkill()._first_youtube_video("https://www.youtube.com/results?search_query=x")
        self.assertEqual(video_id, "dQw4w9WgXcQ")
        self.assertEqual(title, "Titolo del video")

    def test_falls_back_to_a_bare_video_id_without_a_title(self):
        html = 'qualcosa "videoId":"dQw4w9WgXcQ" qualcos altro'
        with self._with_html(html):
            video_id, title = PlayMediaSkill()._first_youtube_video("https://www.youtube.com/results?search_query=x")
        self.assertEqual(video_id, "dQw4w9WgXcQ")
        self.assertIsNone(title)

    def test_no_video_id_in_the_page_returns_none(self):
        with self._with_html("<html>nessun risultato</html>"):
            video_id, title = PlayMediaSkill()._first_youtube_video("https://www.youtube.com/results?search_query=x")
        self.assertIsNone(video_id)

    def test_a_request_failure_returns_none_not_a_crash(self):
        with mock.patch("urllib.request.urlopen", side_effect=Exception("boom")):
            video_id, title = PlayMediaSkill()._first_youtube_video("https://www.youtube.com/results?search_query=x")
        self.assertIsNone(video_id)
        self.assertIsNone(title)


if __name__ == "__main__":
    unittest.main()
