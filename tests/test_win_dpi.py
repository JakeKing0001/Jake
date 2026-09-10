"""Test unitari per core/win_dpi.py: nessuna suite esisteva finora. Le tre API Win32 vengono
mockate direttamente sull'oggetto ctypes.windll.<dll> reale (mock.patch.object sull'attributo
della funzione, ripristinato automaticamente a fine test): non serve un doppio finto di
ctypes.windll, e il codice sotto test resta quello vero, non una sua reimplementazione.

F1: buco reale trovato e corretto in questa sessione. SetProcessDpiAwareness/SetProcessDPIAware
sono API che segnalano il fallimento nel loro VALORE DI RITORNO (un HRESULT diverso da S_OK, o
un BOOL falso), non sollevando un'eccezione - ma il codice chiamava l'API e ritornava
incondizionatamente True, ignorando il valore restituito. Riprodotto per davvero chiamando
SetProcessDpiAwareness due volte di fila nello stesso processo: la seconda chiamata ritorna
E_ACCESSDENIED (0x80070005), non solleva nulla."""
import ctypes
import unittest
from unittest import mock

from core.win_dpi import ensure_dpi_aware

E_ACCESSDENIED = -2147024891  # 0x80070005 come int con segno a 32 bit


class EnsureDpiAwareTests(unittest.TestCase):
    def test_succeeds_via_the_first_api_when_the_context_call_reports_success(self):
        with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", return_value=1) as context_call:
            self.assertTrue(ensure_dpi_aware())
        context_call.assert_called_once()

    def test_falls_back_to_shcore_when_the_context_call_reports_failure(self):
        """SetProcessDpiAwarenessContext ritorna un BOOL: 0 e' un fallimento reale (non
        un'eccezione), deve far proseguire alla API successiva, non fermarsi qui."""
        with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", return_value=0):
            with mock.patch.object(ctypes.windll.shcore, "SetProcessDpiAwareness", return_value=0) as shcore_call:
                self.assertTrue(ensure_dpi_aware())
        shcore_call.assert_called_once_with(2)

    def test_falls_back_to_shcore_when_the_context_api_is_missing(self):
        with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", side_effect=AttributeError()):
            with mock.patch.object(ctypes.windll.shcore, "SetProcessDpiAwareness", return_value=0):
                self.assertTrue(ensure_dpi_aware())

    def test_shcore_failure_hresult_is_not_reported_as_success(self):
        """Il buco reale: prima della correzione, un HRESULT di fallimento (qualunque valore
        diverso da S_OK/0, es. E_ACCESSDENIED se la DPI awareness e' gia' stata impostata per
        un'altra via) veniva ignorato e la funzione ritornava comunque True."""
        with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", return_value=0):
            with mock.patch.object(ctypes.windll.shcore, "SetProcessDpiAwareness", return_value=E_ACCESSDENIED):
                with mock.patch.object(ctypes.windll.user32, "SetProcessDPIAware", return_value=0):
                    self.assertFalse(ensure_dpi_aware())

    def test_falls_back_to_the_legacy_api_when_shcore_fails(self):
        with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", return_value=0):
            with mock.patch.object(ctypes.windll.shcore, "SetProcessDpiAwareness", return_value=E_ACCESSDENIED):
                with mock.patch.object(ctypes.windll.user32, "SetProcessDPIAware", return_value=1) as legacy_call:
                    self.assertTrue(ensure_dpi_aware())
        legacy_call.assert_called_once()

    def test_legacy_api_returning_a_falsy_bool_is_not_reported_as_success(self):
        with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", return_value=0):
            with mock.patch.object(ctypes.windll.shcore, "SetProcessDpiAwareness", return_value=E_ACCESSDENIED):
                with mock.patch.object(ctypes.windll.user32, "SetProcessDPIAware", return_value=0):
                    self.assertFalse(ensure_dpi_aware())

    def test_every_api_missing_returns_false_not_a_crash(self):
        with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", side_effect=AttributeError()):
            with mock.patch.object(ctypes.windll.shcore, "SetProcessDpiAwareness", side_effect=AttributeError()):
                with mock.patch.object(ctypes.windll.user32, "SetProcessDPIAware", side_effect=AttributeError()):
                    self.assertFalse(ensure_dpi_aware())

    def test_an_os_error_from_any_api_is_swallowed_and_the_next_one_is_tried(self):
        with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext", side_effect=OSError()):
            with mock.patch.object(ctypes.windll.shcore, "SetProcessDpiAwareness", side_effect=OSError()):
                with mock.patch.object(ctypes.windll.user32, "SetProcessDPIAware", return_value=1):
                    self.assertTrue(ensure_dpi_aware())

    def test_non_windows_platform_returns_false_without_calling_any_api(self):
        with mock.patch("sys.platform", "linux"):
            with mock.patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext") as context_call:
                self.assertFalse(ensure_dpi_aware())
        context_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
