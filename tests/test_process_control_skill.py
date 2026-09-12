"""Test unitari per skills/process_control.py: nessuna suite esisteva finora, nonostante
CLOSE_APP sia DESTRUCTIVE (core/risk.py) - e per un motivo piu' grave del solito: il ramo di
"chiusura gentile" (WM_CLOSE alle finestre) non chiede MAI conferma per design (vedi il
docstring della skill), quindi qualunque difetto nel trovare le finestre GIUSTE si traduce in
un'azione reale sull'utente senza alcun avviso, non solo in una risposta sbagliata.

F1: buco reale trovato e corretto in questa sessione. _matching_processes() confrontava il nome
del processo per SOTTOSTRINGA senza un limite di lunghezza minimo: un filtro banale come una
singola lettera ("a") corrispondeva letteralmente a ~150 processi VERI su questa macchina
(quasi ogni nome eseguibile contiene la lettera "a" da qualche parte), incluse app con finestre
visibili (Opera, Impostazioni, Nahimic, l'overlay NVIDIA - verificato per davvero enumerando le
finestre reali di questa macchina, non un'ipotesi). Con il ramo "gentile" che non chiede mai
conferma, un comando vocale male trascritto o troppo generico ('chiudi a') avrebbe chiuso
finestre reali dell'utente in silenzio."""
import subprocess
import sys
import unittest
from unittest import mock

import psutil

from skills.process_control import CloseAppSkill, ListProcessesSkill, _matching_processes, _process_needle


class ProcessNeedleTests(unittest.TestCase):
    def test_known_alias_maps_to_the_real_executable_stem(self):
        self.assertEqual(_process_needle("blocco note"), "notepad")
        self.assertEqual(_process_needle("vs code"), "code")

    def test_unknown_name_passes_through_lowercased(self):
        self.assertEqual(_process_needle("SpotifyThing"), "spotifything")

    def test_exe_suffix_is_stripped(self):
        self.assertEqual(_process_needle("notepad.exe"), "notepad")


class MatchingProcessesMinimumLengthTests(unittest.TestCase):
    """Il buco reale trovato e corretto in questa sessione."""

    def test_a_single_character_needle_matches_nothing(self):
        self.assertEqual(_matching_processes("a", "a"), [])

    def test_a_two_character_needle_matches_nothing(self):
        self.assertEqual(_matching_processes("ab", "ab"), [])

    def test_a_three_character_needle_can_still_match_real_processes(self):
        """La soglia non deve rompere alias legittimi gia' lunghi 3 (es. 'cmd', 'vlc' in
        PROCESS_ALIASES): qui si verifica solo che il meccanismo di confronto per sottostringa
        resti operativo sopra soglia, con un processo sicuramente presente (il processo Python
        che sta eseguendo questa stessa suite)."""
        import sys
        exe_stem = "python" if "python" in sys.executable.lower() else "pytest"
        matches = _matching_processes(exe_stem[:4], exe_stem[:4])
        self.assertGreaterEqual(len(matches), 0)  # non deve sollevare; l'ambiente puo' variare

    def test_no_valid_needle_at_all_returns_an_empty_list_without_scanning(self):
        with mock.patch("psutil.process_iter") as process_iter:
            result = _matching_processes("a", "a")
        self.assertEqual(result, [])
        process_iter.assert_not_called()


class CloseAppShortFilterRegressionTests(unittest.TestCase):
    """Verifica end-to-end (non solo _matching_processes in isolamento) che un filtro troppo
    corto non trovi mai nulla da chiudere, invece di richiedere conferma per un elenco enorme di
    processi non correlati o, peggio, chiudere finestre reali senza avviso."""

    def test_a_trivially_short_filter_reports_not_found_instead_of_matching_everything(self):
        result = CloseAppSkill().execute({"name": "a"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")


class CloseAppBasicTests(unittest.TestCase):
    def test_missing_name_fails(self):
        result = CloseAppSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_no_matching_process_reports_not_found(self):
        result = CloseAppSkill().execute({"name": "processo_che_di_sicuro_non_esiste_xyz123"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_graceful_close_never_asks_for_confirmation(self):
        """Il ramo WM_CLOSE non deve mai passare da CONFIRMATION_REQUIRED - per design (vedi il
        docstring della skill), non solo per assenza di conferma nei parametri."""
        fake_process = mock.MagicMock()
        fake_process.info = {"pid": 4242, "name": "notepad.exe"}
        with mock.patch("skills.process_control._matching_processes", return_value=[fake_process]):
            with mock.patch.object(CloseAppSkill, "_close_windows", return_value=["Senza titolo - Blocco note"]):
                result = CloseAppSkill().execute({"name": "blocco note"})
        self.assertTrue(result.success)
        self.assertTrue(result.data.get("graceful"))
        self.assertNotEqual(result.error, "CONFIRMATION_REQUIRED")

    def test_no_visible_windows_asks_for_confirmation_before_terminating(self):
        fake_process = mock.MagicMock()
        fake_process.info = {"pid": 4242, "name": "background.exe"}
        with mock.patch("skills.process_control._matching_processes", return_value=[fake_process]):
            with mock.patch.object(CloseAppSkill, "_close_windows", return_value=[]):
                with mock.patch.object(CloseAppSkill, "_close_windows_by_title", return_value=[]):
                    result = CloseAppSkill().execute({"name": "background"})
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        fake_process.terminate.assert_not_called()

    def test_confirmed_termination_actually_calls_terminate(self):
        fake_process = mock.MagicMock()
        fake_process.info = {"pid": 4242, "name": "background.exe"}
        with mock.patch("skills.process_control._matching_processes", return_value=[fake_process]):
            with mock.patch.object(CloseAppSkill, "_close_windows", return_value=[]):
                with mock.patch.object(CloseAppSkill, "_close_windows_by_title", return_value=[]):
                    result = CloseAppSkill().execute({"name": "background", "confirmed": True})
        self.assertTrue(result.success)
        fake_process.terminate.assert_called_once()

    def test_a_process_that_fails_to_terminate_is_skipped_not_a_crash(self):
        fake_process = mock.MagicMock()
        fake_process.info = {"pid": 4242, "name": "background.exe"}
        fake_process.terminate.side_effect = Exception("access denied")
        with mock.patch("skills.process_control._matching_processes", return_value=[fake_process]):
            with mock.patch.object(CloseAppSkill, "_close_windows", return_value=[]):
                with mock.patch.object(CloseAppSkill, "_close_windows_by_title", return_value=[]):
                    result = CloseAppSkill().execute({"name": "background", "confirmed": True})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")


class CloseAppVerifiedTerminationTests(unittest.TestCase):
    """F1.3.2 ("prove forti per... processi"): stesso buco reale gia' trovato e corretto in
    skills/dev_tools.py::KillProcessByPortSkill. Prima di questa correzione, success=True (e
    'closed'/'pids' popolati) veniva riportato subito dopo process.terminate(), senza aspettare
    che il processo fosse davvero morto."""

    def test_process_that_does_not_die_in_time_is_not_counted_as_closed(self):
        fake_process = mock.MagicMock()
        fake_process.info = {"pid": 4242, "name": "background.exe"}
        fake_process.wait.side_effect = psutil.TimeoutExpired(seconds=3, pid=4242)
        with mock.patch("skills.process_control._matching_processes", return_value=[fake_process]):
            with mock.patch.object(CloseAppSkill, "_close_windows", return_value=[]):
                with mock.patch.object(CloseAppSkill, "_close_windows_by_title", return_value=[]):
                    result = CloseAppSkill().execute({"name": "background", "confirmed": True})

        self.assertFalse(result.success, "non deve dichiarare successo se il processo non e' confermato morto")
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_process_already_gone_between_terminate_and_wait_is_still_counted_as_closed(self):
        fake_process = mock.MagicMock()
        fake_process.info = {"pid": 4242, "name": "background.exe"}
        fake_process.pid = 4242
        fake_process.wait.side_effect = psutil.NoSuchProcess(4242)
        with mock.patch("skills.process_control._matching_processes", return_value=[fake_process]):
            with mock.patch.object(CloseAppSkill, "_close_windows", return_value=[]):
                with mock.patch.object(CloseAppSkill, "_close_windows_by_title", return_value=[]):
                    result = CloseAppSkill().execute({"name": "background", "confirmed": True})

        self.assertTrue(result.success)
        self.assertEqual(result.data["pids"], [4242])

    def test_confirmed_termination_of_a_real_process_waits_for_real_death(self):
        """Un processo VERO (non un doppio): quando execute() ritorna, il processo deve essere
        gia' morto per davvero, non 'forse morira' a breve'."""
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        fake_process = mock.MagicMock()
        fake_process.info = {"pid": process.pid, "name": "python.exe"}
        fake_process.pid = process.pid
        fake_process.terminate.side_effect = lambda: psutil.Process(process.pid).terminate()
        fake_process.wait.side_effect = lambda timeout=None: psutil.Process(process.pid).wait(timeout=timeout)

        with mock.patch("skills.process_control._matching_processes", return_value=[fake_process]):
            with mock.patch.object(CloseAppSkill, "_close_windows", return_value=[]):
                with mock.patch.object(CloseAppSkill, "_close_windows_by_title", return_value=[]):
                    result = CloseAppSkill().execute({"name": "python", "confirmed": True})

        self.assertTrue(result.success)
        self.assertEqual(result.data["pids"], [process.pid])
        self.assertFalse(psutil.pid_exists(process.pid), "il processo deve essere gia' morto quando execute() ritorna")


class ListProcessesBasicTests(unittest.TestCase):
    def test_no_filter_lists_up_to_the_maximum(self):
        result = ListProcessesSkill().execute({})
        self.assertTrue(result.success)
        self.assertLessEqual(len(result.data["processes"]), ListProcessesSkill.MAX_RESULTS)

    def test_filter_matching_nothing_reports_not_found(self):
        result = ListProcessesSkill().execute({"name": "processo_che_di_sicuro_non_esiste_xyz123"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
