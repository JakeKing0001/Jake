"""Test unitari per core/windows_hello.py (F1, Trustworthy Agent Core 3.0). is_available() e'
sicura da chiamare per davvero (nessun prompt, solo un controllo). verify() invece mostra un
prompt nativo di Windows Hello e aspetta l'utente: MAI chiamata qui senza sostituire
_verified_synchronously con un finto - vedi il modulo per il perche' (un primo tentativo di
mockare l'import di winsdk in sys.modules non ha funzionato, e ha fatto scattare un prompt vero
durante `python -m unittest` la prima volta che questo file e' stato scritto)."""
import unittest

from core.windows_hello import is_available, verify


class RealAvailabilityCheckTests(unittest.TestCase):
    def test_availability_check_runs_for_real_without_a_prompt(self):
        result = is_available()
        self.assertIsInstance(result, bool)


class VerifyTests(unittest.TestCase):
    """_verified_synchronously e' SEMPRE sostituito qui: nessuno di questi test deve poter
    toccare winsdk/l'API vera, nemmeno per errore."""

    def test_true_when_the_injected_check_succeeds(self):
        result = verify("Spegni il computer", _verified_synchronously=lambda reason: True)
        self.assertTrue(result)

    def test_false_when_the_injected_check_fails(self):
        result = verify("Spegni il computer", _verified_synchronously=lambda reason: False)
        self.assertFalse(result)

    def test_reason_is_forwarded_to_the_injected_check(self):
        received = []
        verify("Esegui questo comando", _verified_synchronously=lambda reason: received.append(reason) or True)
        self.assertEqual(received, ["Esegui questo comando"])

    def test_any_exception_degrades_to_false_instead_of_raising(self):
        """Principio "degrado elegante" della roadmap: winsdk mancante, un errore WinRT
        imprevisto, qualunque cosa - dal punto di vista di chi chiama e' "non autorizzato",
        mai un crash a meta' di un'azione ADMIN."""
        def _raise(reason):
            raise RuntimeError("WinRT non disponibile in questo ambiente")

        result = verify("Spegni il computer", _verified_synchronously=_raise)

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
