"""Test per core/computer_use/browser_adapter.py (F3.6.1/F3.6.2, prima fetta di F3.6 - mai
iniziata prima d'ora). Lancia DAVVERO Edge contro benchmarks/browser_fixture.html in un processo
separato, con un profilo ISOLATO (mai il profilo reale dell'utente - vedi il docstring del modulo
per il rischio di privacy gia' trovato e la ragione dei flag di isolamento). Saltato
esplicitamente se Edge non e' installato in questo ambiente (`BrowserNotFoundError`), non fatto
fallire - lo stesso principio gia' seguito per l'OCR (F3.5.1, `core/vision/screen.py::
ocr_available`)."""
import unittest
from pathlib import Path

from core.computer_use.browser_adapter import (
    BrowserNotFoundError,
    find_edge_executable,
    find_page_document,
    launch_isolated_browser,
)
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter

_FIXTURE_URL = f"file:///{(Path(__file__).resolve().parent.parent / 'benchmarks' / 'browser_fixture.html').as_posix()}"


def _edge_available() -> bool:
    try:
        find_edge_executable()
        return True
    except BrowserNotFoundError:
        return False


@unittest.skipUnless(_edge_available(), "Microsoft Edge non e' installato in questo ambiente")
class RealBrowserFixtureTests(unittest.TestCase):
    def setUp(self):
        self.browser = launch_isolated_browser(_FIXTURE_URL)
        self.addCleanup(self.browser.terminate_and_cleanup)
        self.adapter = UIAutomationAdapter()
        self.window = self.adapter.find_window_by_process_id(self.browser.process.pid, timeout_seconds=15.0)

    def test_the_page_content_is_not_visible_before_waking_the_accessibility_tree(self):
        """Buco reale trovato investigando, non ipotizzato (vedi il docstring del modulo): prima
        di una ricerca dedicata, il contenuto della pagina - anche gia' completamente caricato -
        non e' presente nell'albero UI Automation, solo il chrome del browser lo e'."""
        matches = self.adapter.find_matching_elements(self.window, control_type="Document")
        self.assertEqual(matches, [], "il nodo Document non deve essere gia' presente prima della prima sveglia")

    def test_find_page_document_wakes_the_tree_and_returns_the_page_root(self):
        document = find_page_document(self.adapter, self.window)

        info = self.adapter.describe_element(document)
        self.assertEqual(info.control_type, "Document")

    def test_the_real_page_elements_are_found_inside_the_document(self):
        document = find_page_document(self.adapter, self.window)
        engine = SelectorEngine(self.adapter)

        button = engine.wait_for_unique_element(document, ElementSelector(name="Aggiungi", control_type="Button"))
        input_field = engine.wait_for_unique_element(document, ElementSelector(name="Campo di testo", control_type="Edit"))
        link = engine.wait_for_unique_element(document, ElementSelector(name="Un link di prova", control_type="Hyperlink"))

        self.assertEqual(button.CurrentName, "Aggiungi")
        self.assertEqual(input_field.CurrentName, "Campo di testo")
        self.assertEqual(link.CurrentName, "Un link di prova")

    def test_browser_chrome_elements_are_not_inside_the_document(self):
        """F3.6.2 ("distinguere contenuto pagina e browser chrome"): un bottone del CHROME del
        browser (es. "Nuova scheda") non deve comparire cercando SOLO dentro il `Document` - la
        separazione e' strutturale (il confine del nodo `Document`), non un elenco di nomi
        "chrome" da escludere a mano."""
        document = find_page_document(self.adapter, self.window)

        matches = self.adapter.find_matching_elements(document, name="Nuova scheda")
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
