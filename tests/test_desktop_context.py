"""Test unitari per il World Context Engine leggero (v3.5, core/desktop_context.py): finestre
aperte e anteprima appunti raccolte in background. Chiama direttamente i metodi privati di
polling (stesso stile di tests/test_system_advisor.py) invece di far girare il thread reale, con
mock.patch sulle chiamate di sistema (win32gui/win32clipboard) cosi' i test non toccano il
desktop vero e non dipendono da cosa e' aperto sulla macchina in quel momento."""
import time
import unittest
from unittest import mock

from core.desktop_context import DesktopContextTracker


class OpenWindowsPollingTests(unittest.TestCase):
    def setUp(self):
        self.tracker = DesktopContextTracker()

    def test_open_windows_are_stored(self):
        with mock.patch("core.vision.screen.list_open_window_titles", return_value=["Blocco note", "Spotify"]):
            self.tracker._poll_open_windows()
        self.assertEqual(self.tracker.get_open_windows(), ["Blocco note", "Spotify"])

    def test_failure_leaves_previous_value_untouched(self):
        with mock.patch("core.vision.screen.list_open_window_titles", return_value=["Blocco note"]):
            self.tracker._poll_open_windows()
        with mock.patch("core.vision.screen.list_open_window_titles", side_effect=RuntimeError("boom")):
            self.tracker._poll_open_windows()  # non deve sollevare
        self.assertEqual(self.tracker.get_open_windows(), ["Blocco note"])


class ClipboardPollingTests(unittest.TestCase):
    def setUp(self):
        self.tracker = DesktopContextTracker()

    def test_short_text_is_stored_verbatim(self):
        with mock.patch("win32clipboard.OpenClipboard"), mock.patch("win32clipboard.CloseClipboard"), \
             mock.patch("win32clipboard.GetClipboardData", return_value="ciao mondo"):
            self.tracker._poll_clipboard()
        self.assertEqual(self.tracker.get_clipboard_preview(), "ciao mondo")

    def test_long_text_is_truncated_with_ellipsis(self):
        long_text = "a" * 500
        with mock.patch("win32clipboard.OpenClipboard"), mock.patch("win32clipboard.CloseClipboard"), \
             mock.patch("win32clipboard.GetClipboardData", return_value=long_text):
            self.tracker._poll_clipboard()
        preview = self.tracker.get_clipboard_preview()
        self.assertTrue(preview.endswith("…"))
        self.assertLess(len(preview), 500)

    def test_empty_clipboard_yields_no_preview(self):
        with mock.patch("win32clipboard.OpenClipboard"), mock.patch("win32clipboard.CloseClipboard"), \
             mock.patch("win32clipboard.GetClipboardData", return_value=""):
            self.tracker._poll_clipboard()
        self.assertIsNone(self.tracker.get_clipboard_preview())

    def test_clipboard_error_yields_no_preview_and_does_not_raise(self):
        with mock.patch("win32clipboard.OpenClipboard", side_effect=RuntimeError("occupato")):
            self.tracker._poll_clipboard()  # non deve sollevare
        self.assertIsNone(self.tracker.get_clipboard_preview())


class ContextSummaryTests(unittest.TestCase):
    def test_summary_combines_all_available_pieces(self):
        tracker = DesktopContextTracker()
        with mock.patch("core.vision.screen.get_active_window_title", return_value="Visual Studio Code"):
            tracker._poll_active_window()
        with mock.patch("core.vision.screen.list_open_window_titles", return_value=["Visual Studio Code", "Spotify"]):
            tracker._poll_open_windows()
        with mock.patch("win32clipboard.OpenClipboard"), mock.patch("win32clipboard.CloseClipboard"), \
             mock.patch("win32clipboard.GetClipboardData", return_value="un indirizzo email"):
            tracker._poll_clipboard()

        summary = tracker.context_summary()
        self.assertIn("Visual Studio Code", summary)
        self.assertIn("Spotify", summary)
        self.assertIn("un indirizzo email", summary)

    def test_empty_tracker_yields_empty_summary(self):
        tracker = DesktopContextTracker()
        self.assertEqual(tracker.context_summary(), "")


class StopTimeoutTests(unittest.TestCase):
    """F1.8.5 ("aggiungere deadlock timeout e diagnosi"): buco reale, riprodotto per davvero -
    se _run() era bloccato oltre i 2s di timeout di stop(), join() tornava comunque,
    silenziosamente, senza dire che il thread era ANCORA vivo."""

    def test_stop_logs_a_warning_when_the_thread_does_not_stop_in_time(self):
        tracker = DesktopContextTracker(poll_seconds=100, stop_timeout_seconds=0.05)
        tracker._logger = mock.Mock()
        with mock.patch.object(tracker, "_poll_active_window", side_effect=lambda: time.sleep(0.5)):
            tracker.start()
            self.addCleanup(lambda: tracker._thread.join(timeout=5))
            time.sleep(0.05)  # lascia partire _run() e bloccarsi dentro _poll_active_window()

            tracker.stop()

        self.assertTrue(tracker._thread.is_alive(), "il thread deve essere ancora bloccato a questo punto")
        tracker._logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
