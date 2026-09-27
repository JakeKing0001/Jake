"""F5.6.4/F5.6.7: il contesto del desktop non presenta come "adesso" un segnale non piu' riletto, e non legge
gli appunti quando l'app che ha copiato (password manager, campo password) chiede a Windows di non monitorarli.
Appunti VERI di Windows (il contenuto dell'utente viene salvato e rimesso a posto)."""
import unittest

from core.desktop_context import DesktopContextTracker

try:
    import win32clipboard
except ImportError:  # pragma: no cover - solo Windows
    win32clipboard = None


@unittest.skipIf(win32clipboard is None, "serve pywin32 (Windows)")
class ClipboardPrivacyTests(unittest.TestCase):
    def setUp(self):
        win32clipboard.OpenClipboard()
        try:
            self.saved = (win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                          if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT) else None)
        finally:
            win32clipboard.CloseClipboard()
        self.addCleanup(self._restore)

    def _restore(self):
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            if self.saved is not None:
                win32clipboard.SetClipboardText(self.saved, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()

    @staticmethod
    def _copy(text, private):
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            if private:  # quello che fanno i password manager
                win32clipboard.SetClipboardData(
                    win32clipboard.RegisterClipboardFormat("ExcludeClipboardContentFromMonitorProcessing"), b"\0")
        finally:
            win32clipboard.CloseClipboard()

    def test_a_password_copied_by_a_password_manager_is_never_read(self):
        tracker = DesktopContextTracker()
        self._copy("S3gret0-di-prova!", private=True)
        tracker._poll_clipboard()
        self.assertIsNone(tracker.get_clipboard_preview())
        self.assertIn("Appunti: contenuto privato (non letto)", tracker.context_summary())
        self.assertNotIn("S3gret0", tracker.context_summary())

        self._copy("testo normale da riassumere", private=False)
        tracker._poll_clipboard()
        self.assertIn('Appunti: "testo normale da riassumere"', tracker.context_summary())


class StaleSignalTests(unittest.TestCase):
    def test_a_signal_not_read_again_stops_being_presented_as_current(self):
        now = [100.0]
        tracker = DesktopContextTracker(poll_seconds=3.0, clock=lambda: now[0])
        with tracker._lock:
            tracker._open_windows = ["Documento - Word"]
            tracker._fresh_at["open_windows"] = now[0]
        self.assertIn("Finestre aperte ora: Documento - Word", tracker.context_summary())
        now[0] += tracker.stale_after_seconds + 1  # le letture successive falliscono: nessun aggiornamento
        self.assertNotIn("Finestre aperte ora", tracker.context_summary())


if __name__ == "__main__":
    unittest.main()
