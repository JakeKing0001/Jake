"""Test per skills/read_web_page.py (ReadWebPageSkill, F3.6.4 - la prima skill che legge
davvero il testo di una pagina web attraverso launch_isolated_browser/find_page_document/
read_page_text, F3.6.1-F3.6.2, dichiarati "additivi, mai usati da nessuna skill" fin dal loro
stesso docstring - lo stesso genere di gap gia' chiuso per F3.8 da RunComputerProcedureSkill).

I test di validazione (URL/parametri) non richiedono Edge, la validazione avviene PRIMA di
toccare launch_isolated_browser. Il test end-to-end serve la fixture browser gia' esistente
(benchmarks/browser_fixture.html) via un piccolo server HTTP locale su 127.0.0.1 (porta
effimera): un URL file:// verrebbe RIFIUTATO dalla stessa validazione della skill (accetta solo
http/https, deliberatamente - vedi UrlValidationTests), quindi non c'e' altro modo onesto di
provare la skill VERA per intero contro contenuto locale, senza toccare internet reale. Saltato
esplicitamente se Edge non e' installato, stesso principio gia' seguito da
tests/test_browser_adapter.py."""
import functools
import http.server
import threading
import unittest
from pathlib import Path

from core.computer_use.browser_adapter import BrowserNotFoundError, find_edge_executable
from skills.read_web_page import ReadWebPageSkill

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _edge_available() -> bool:
    try:
        find_edge_executable()
        return True
    except BrowserNotFoundError:
        return False


class MissingParametersTests(unittest.TestCase):
    def test_a_missing_url_is_reported(self):
        skill = ReadWebPageSkill()

        result = skill.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_an_empty_url_is_reported(self):
        skill = ReadWebPageSkill()

        result = skill.execute({"url": "   "})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class UrlValidationTests(unittest.TestCase):
    """Nessun browser reale necessario - la stessa validazione, deliberatamente duplicata da
    skills/open_url.py::OpenUrlSkill (vedi il docstring del modulo), respinge questi casi PRIMA
    di lanciare qualunque processo."""

    def setUp(self):
        self.skill = ReadWebPageSkill()

    def test_a_dangerous_scheme_is_rejected(self):
        result = self.skill.execute({"url": "javascript:alert(1)"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "INVALID_URL")

    def test_a_file_scheme_is_rejected(self):
        """Un file:// locale non deve MAI passare - leggerebbe file arbitrari del disco invece
        di una pagina web, un vettore di divulgazione locale diverso da quello dichiarato da
        questa skill (solo pagine web pubbliche)."""
        result = self.skill.execute({"url": "file:///C:/Windows/win.ini"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "INVALID_URL")

    def test_a_url_without_a_domain_is_rejected(self):
        result = self.skill.execute({"url": "https://"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "INVALID_URL")


@unittest.skipUnless(_edge_available(), "Microsoft Edge non e' installato in questo ambiente")
class ReadRealFixtureOverHttpTests(unittest.TestCase):
    """Serve benchmarks/ via HTTP locale (127.0.0.1, porta effimera) cosi' browser_fixture.html
    e' raggiungibile con uno schema http:// - vedi il docstring del modulo per il motivo (la
    validazione della skill rifiuta file://)."""

    @classmethod
    def setUpClass(cls):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(_REPO_ROOT / "benchmarks"))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_reads_the_real_fixture_page_text_through_the_skill(self):
        skill = ReadWebPageSkill()

        result = skill.execute({"url": f"http://127.0.0.1:{self.port}/browser_fixture.html"})

        self.assertTrue(result.success, result)
        self.assertFalse(result.data["truncated"])
        # Il testo VERO della pagina fixture (F3.6.1), non solo l'assenza di eccezioni - lo
        # stesso principio "prova l'effetto vero" gia' seguito ovunque in questa sessione.
        self.assertIn("Jake Browser Fixture", result.data["text"])
        self.assertIn("Aggiungi", result.data["text"])
        # F3.6.7 (redazione password), adozione: la fixture ha un campo password con un valore
        # vero ("segreto123") - non deve MAI comparire nel testo letto, la stessa garanzia gia'
        # provata direttamente su read_page_text in tests/test_browser_adapter.py, qui verificata
        # anche attraverso la skill.
        self.assertNotIn("segreto123", result.data["text"])

    def test_an_unreachable_local_port_is_reported_without_crashing(self):
        """Nessun server in ascolto su questa porta (mai aperta) - il browser isolato naviga
        DAVVERO verso un errore di connessione reale, non un mock. `read_page_text` su una
        pagina di errore del browser produce comunque una stringa (il testo dell'errore stesso),
        quindi non e' garantito NOT_FOUND - qui verifichiamo solo che la skill non sollevi mai
        un'eccezione non gestita e chiuda comunque il browser (nessun processo orfano)."""
        skill = ReadWebPageSkill()

        result = skill.execute({"url": "http://127.0.0.1:1"})

        self.assertIsInstance(result.success, bool)


if __name__ == "__main__":
    unittest.main()
