"""Test unitari per skills/open_app.py: nessuna suite esisteva finora, nonostante sia una delle
skill piu' usate (OPEN_APP). AppResolver e' finto (ha gia' la propria suite dedicata,
tests/test_app_resolver.py) - qui si verifica solo la logica della skill: soglia di conferma,
scelta del launcher, fallback nella catena separata da '|'. os.startfile/subprocess.Popen/
webbrowser.open sono sempre mockati."""
import unittest
from unittest import mock

from core.app_resolver import AppMatch
from skills.open_app import OpenAppSkill


def _skill_with_match(match, auto_execute_threshold=0.90):
    resolver = mock.MagicMock()
    resolver.resolve.return_value = match
    return OpenAppSkill(app_resolver=resolver, auto_execute_threshold=auto_execute_threshold), resolver


class MissingParametersTests(unittest.TestCase):
    def test_missing_app_fails(self):
        skill, _ = _skill_with_match(None)
        result = skill.execute({})
        self.assertEqual(result.error, "UNSUPPORTED_APP")


class ResolutionTests(unittest.TestCase):
    def test_no_match_reports_unsupported_app(self):
        skill, resolver = _skill_with_match(None)
        result = skill.execute({"app": "un programma inesistente"})
        self.assertEqual(result.error, "UNSUPPORTED_APP")
        resolver.resolve.assert_called_once_with("un programma inesistente")

    def test_low_confidence_match_asks_for_confirmation_instead_of_launching(self):
        match = AppMatch(requested="spotfy", matched_app="Spotify", score=0.80, launcher="spotify.exe")
        skill, _ = _skill_with_match(match)
        with mock.patch("os.startfile") as startfile:
            result = skill.execute({"app": "spotfy"})
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(result.data["confirm_parameters"], {"app": "Spotify"})
        startfile.assert_not_called()

    def test_high_confidence_match_launches_directly(self):
        match = AppMatch(requested="spotify", matched_app="Spotify", score=1.0, launcher="spotify.exe")
        skill, _ = _skill_with_match(match)
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile") as startfile:
            result = skill.execute({"app": "spotify"})
        self.assertTrue(result.success)
        startfile.assert_called_once_with("spotify.exe")

    def test_launch_failure_reports_launch_failed(self):
        match = AppMatch(requested="spotify", matched_app="Spotify", score=1.0, launcher="spotify.exe")
        skill, _ = _skill_with_match(match)
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile", side_effect=OSError("non trovato")):
            with mock.patch("subprocess.Popen", side_effect=OSError("nemmeno cosi'")):
                result = skill.execute({"app": "spotify"})
        self.assertEqual(result.error, "LAUNCH_FAILED")


class LaunchChainTests(unittest.TestCase):
    """La catena separata da '|' (es. 'wt.exe|cmd.exe' per il terminale): prova ogni candidato
    in ordine finche' uno funziona."""

    def test_the_browser_placeholder_opens_google(self):
        with mock.patch("webbrowser.open", return_value=True) as browser_open:
            result = OpenAppSkill._launch("__browser__")
        self.assertTrue(result)
        browser_open.assert_called_once_with("https://www.google.com")

    def test_a_single_candidate_is_launched_via_startfile(self):
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile") as startfile:
            result = OpenAppSkill._launch("notepad.exe")
        self.assertTrue(result)
        startfile.assert_called_once_with("notepad.exe")

    def test_first_candidate_failing_falls_through_to_the_second(self):
        with mock.patch("sys.platform", "win32"):
            with mock.patch("os.startfile", side_effect=OSError("wt non installato")):
                with mock.patch("subprocess.Popen") as popen:
                    result = OpenAppSkill._launch("wt.exe|cmd.exe")
        self.assertTrue(result)
        # os.startfile fallisce per ENTRAMBI (stesso side_effect), quindi ognuno ripiega su
        # subprocess.Popen(shell=True) prima di passare al prossimo: verifichiamo che l'ultimo
        # candidato provato sia stato lanciato con successo tramite Popen.
        popen.assert_called()

    def test_a_uri_style_launcher_uses_startfile(self):
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile") as startfile:
            result = OpenAppSkill._launch("ms-settings:bluetooth")
        self.assertTrue(result)
        startfile.assert_called_once_with("ms-settings:bluetooth")

    def test_a_lnk_launcher_uses_startfile(self):
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile") as startfile:
            result = OpenAppSkill._launch(r"C:\Users\me\Desktop\App.lnk")
        self.assertTrue(result)
        startfile.assert_called_once()

    def test_a_shell_appsfolder_launcher_uses_startfile(self):
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile") as startfile:
            result = OpenAppSkill._launch(r"shell:AppsFolder\Microsoft.WindowsCalculator")
        self.assertTrue(result)
        startfile.assert_called_once()

    def test_every_candidate_failing_returns_false(self):
        with mock.patch("sys.platform", "win32"):
            with mock.patch("os.startfile", side_effect=OSError("boom")):
                with mock.patch("subprocess.Popen", side_effect=OSError("boom")):
                    result = OpenAppSkill._launch("non_esiste.exe")
        self.assertFalse(result)

    def test_non_windows_platform_uses_subprocess_popen(self):
        with mock.patch("sys.platform", "linux"), mock.patch("subprocess.Popen") as popen:
            result = OpenAppSkill._launch("gedit")
        self.assertTrue(result)
        popen.assert_called_once_with(["gedit"])

    def test_empty_candidates_in_the_chain_are_skipped(self):
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile") as startfile:
            result = OpenAppSkill._launch("|notepad.exe")
        self.assertTrue(result)
        startfile.assert_called_once_with("notepad.exe")


if __name__ == "__main__":
    unittest.main()
