"""Test per core/computer_use/browser_adapter.py (F3.6.1/F3.6.2, prima fetta di F3.6 - mai
iniziata prima d'ora). Lancia DAVVERO Edge contro benchmarks/browser_fixture.html in un processo
separato, con un profilo ISOLATO (mai il profilo reale dell'utente - vedi il docstring del modulo
per il rischio di privacy gia' trovato e la ragione dei flag di isolamento). Saltato
esplicitamente se Edge non e' installato in questo ambiente (`BrowserNotFoundError`), non fatto
fallire - lo stesso principio gia' seguito per l'OCR (F3.5.1, `core/vision/screen.py::
ocr_available`)."""
import unittest
from pathlib import Path

from core.computer_agent import ComputerAgent
from core.computer_use.browser_adapter import (
    BrowserNotFoundError,
    find_edge_executable,
    find_page_document,
    is_password_field,
    launch_isolated_browser,
    read_address_bar_text,
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

    def test_computer_agent_click_and_type_work_against_the_document_root(self):
        """F3.4.2 + F3.6 insieme: `ComputerAgent.type_into_element`/`click_element` con `root`
        (il nodo `Document`, non `window_title` - un browser non ne ha uno prevedibile) invece di
        assemblare adapter/selector/executor a mano - lo stesso genere di dimostrazione end-to-end
        gia' fatta per la fixture Qt in `tests/test_computer_use_integration.py`, qui contro un
        browser vero."""
        document = find_page_document(self.adapter, self.window)
        agent = ComputerAgent()

        type_result = agent.type_into_element("dal browser", root=document, name="Campo di testo", control_type="Edit")
        self.assertTrue(type_result.success)
        click_result = agent.click_element(root=document, name="Aggiungi", control_type="Button")
        self.assertTrue(click_result.success)

        # Il gestore onclick della fixture scrive il valore del campo nel paragrafo di output -
        # rileggere il Document (potrebbe essere cambiato) e cercare quel testo e' la prova
        # indipendente che l'intera catena ha avuto un effetto reale, non solo che nessuna delle
        # due chiamate abbia sollevato.
        document_after = find_page_document(self.adapter, self.window)
        matches = self.adapter.find_matching_elements(document_after, name="dal browser")
        self.assertEqual(len(matches), 1, "il paragrafo di output deve mostrare davvero il testo digitato")

    def test_read_address_bar_text_shows_a_normalized_path_not_the_exact_url(self):
        """F3.6.5 (prima fetta - "verificare URL"): buco reale trovato leggendo davvero la barra
        degli indirizzi, non ipotizzato - per un `file:///` locale Edge la mostra NORMALIZZATA
        (percorso Windows con `/`, senza lo schema `file:///` davanti), diversa carattere per
        carattere dall'URL passato a `launch_isolated_browser`. Verificato che il pezzo
        DISTINTIVO (il nome del file) sia comunque presente - la sottostringa e' il confronto
        onesto, mai l'uguaglianza esatta con questo genere di URL."""
        text = read_address_bar_text(self.adapter, self.window)

        self.assertIsNotNone(text)
        self.assertIn("browser_fixture.html", text)
        self.assertNotEqual(text, _FIXTURE_URL, "la barra normalizza il file:// - non deve mai coincidere per uguaglianza esatta")

    def test_is_password_field_distinguishes_a_real_password_field_from_a_normal_one(self):
        """F3.6.7 (prima fetta - "redigere password e campi sensibili"): segnale STRUTTURALE
        (`CurrentIsPassword`), non un'euristica sul nome del campo - verificato il contrasto tra
        i due campi della fixture, non assunto da uno solo."""
        document = find_page_document(self.adapter, self.window)
        engine = SelectorEngine(self.adapter)

        password_field = engine.wait_for_unique_element(document, ElementSelector(name="Password", control_type="Edit"))
        normal_field = engine.wait_for_unique_element(document, ElementSelector(name="Campo di testo", control_type="Edit"))

        self.assertTrue(is_password_field(password_field))
        self.assertFalse(is_password_field(normal_field))

    def test_a_real_password_fields_value_is_already_masked_by_chromium_not_by_this_module(self):
        """Rassicurazione reale, non assunta: il campo password della fixture ha un valore VERO
        ("segreto123", vedi benchmarks/browser_fixture.html) - se questo test leggesse quel
        valore in chiaro, sarebbe un buco di sicurezza reale. Chromium lo maschera GIA' a livello
        di UI Automation, prima che questo modulo debba fare qualunque cosa."""
        import comtypes
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA

        document = find_page_document(self.adapter, self.window)
        engine = SelectorEngine(self.adapter)
        password_field = engine.wait_for_unique_element(document, ElementSelector(name="Password", control_type="Edit"))

        value_pattern = password_field.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)

        self.assertNotEqual(value_pattern.CurrentValue, "segreto123", "il valore vero non deve mai essere leggibile via UI Automation")

    def test_clicking_a_real_link_navigates_to_a_second_local_page(self):
        """F3.6.3 (prima fetta - "supportare navigazione"): nessun codice nuovo necessario - la
        composizione gia' esistente di `click_element` (F3.4.2/F3.6, Invoke su un `Hyperlink`) +
        `read_address_bar_text` (F3.6.5) + `find_page_document` (F3.6.1) basta gia' a dimostrare
        una navigazione VERA (non un'ancora "#" sulla stessa pagina - il link della fixture punta
        a `browser_fixture_page2.html`, una seconda pagina locale reale)."""
        document = find_page_document(self.adapter, self.window)
        agent = ComputerAgent()

        click_result = agent.click_element(root=document, name="Un link di prova", control_type="Hyperlink")
        self.assertTrue(click_result.success)

        address_after = read_address_bar_text(self.adapter, self.window)
        self.assertIn("browser_fixture_page2.html", address_after)

        new_document = find_page_document(self.adapter, self.window)
        info = self.adapter.describe_element(new_document)
        self.assertEqual(info.name, "Jake Browser Fixture - Pagina 2", "la pagina caricata deve essere davvero la seconda, non la stessa")


if __name__ == "__main__":
    unittest.main()
