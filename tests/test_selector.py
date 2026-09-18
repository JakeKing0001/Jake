"""Test unitari per core/computer_use/selector.py (F3.3.1-F3.3.3, prima fetta del selector
engine - mai iniziata prima d'ora). Stesso schema di tests/test_ui_automation_adapter.py: lancia
davvero la fixture di F3.1.1 in un processo separato e cerca elementi con UI Automation vera - un
selettore che "funziona" solo contro un albero finto (ElementInfo costruiti a mano) non
proverebbe che le condizioni COM native (FindAll + CreateAndCondition) sono corrette, il punto
centrale di questo modulo."""
import unittest

from core.computer_use.selector import AmbiguousSelectionError, ElementSelector, NoMatchError, SelectorEngine
from tests.test_ui_automation_adapter import _RealFixtureTestCase


class ElementSelectorTests(unittest.TestCase):
    """F3.3.1: un selettore senza alcun criterio non ha senso - "qualunque elemento" non e' una
    ricerca, e' l'assenza di una."""

    def test_a_selector_with_no_criteria_at_all_is_rejected(self):
        with self.assertRaises(ValueError):
            ElementSelector()

    def test_a_selector_with_only_a_name_is_accepted(self):
        ElementSelector(name="Aggiungi")

    def test_a_selector_with_only_a_control_type_is_accepted(self):
        ElementSelector(control_type="Button")

    def test_a_selector_with_only_an_automation_id_is_accepted(self):
        ElementSelector(automation_id="fixture_add_button")


class FindUniqueAgainstTheRealFixtureTests(_RealFixtureTestCase):
    def setUp(self):
        self.engine = SelectorEngine(self.adapter)

    def test_a_selector_matching_exactly_one_element_returns_it(self):
        info = self.engine.find_unique(self.window, ElementSelector(name="Aggiungi", control_type="Button"))

        self.assertEqual(info.name, "Aggiungi")
        self.assertTrue(info.automation_id.endswith("fixture_add_button"))

    def test_automation_id_alone_is_enough_to_disambiguate(self):
        """L'automation_id di Qt e' un percorso QUALIFICATO (F3.2, gia' documentato) - sempre
        unico anche quando il nome da solo non lo sarebbe (es. 'Sistema' compare piu' volte nel
        chrome nativo della finestra). CreatePropertyCondition confronta per uguaglianza ESATTA
        (non una sottostringa), quindi serve il percorso qualificato per intero, non solo la
        parte finale - trovato riproducendo l'errore (NoMatchError con la sola parte finale)
        prima di correggere il test, non assunto."""
        info = self.engine.find_unique(
            self.window,
            ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_reset_button"),
        )

        self.assertEqual(info.name, "Reset")

    def test_a_selector_matching_nothing_raises_no_match(self):
        with self.assertRaises(NoMatchError):
            self.engine.find_unique(self.window, ElementSelector(name="Questo elemento non esiste XYZ"))

    def test_a_selector_matching_several_elements_raises_ambiguous(self):
        """Le due voci dell'albero ('Categoria A'/'Categoria B') condividono lo stesso
        control_type - cercare solo per TreeItem, senza un nome, e' deliberatamente ambiguo."""
        with self.assertRaises(AmbiguousSelectionError):
            self.engine.find_unique(self.window, ElementSelector(control_type="TreeItem"))

    def test_find_all_returns_every_matching_element_not_just_one(self):
        buttons = self.engine.find_all(self.window, ElementSelector(control_type="Button"))

        names = {b.name for b in buttons}
        self.assertIn("Aggiungi", names)
        self.assertIn("Reset", names)
        self.assertIn("Rimuovi selezionato", names)
        self.assertGreater(len(buttons), 3, "anche i bottoni del chrome nativo (riduci/ingrandisci/chiudi) devono comparire")

    def test_find_all_with_no_matches_returns_an_empty_list_not_an_error(self):
        result = self.engine.find_all(self.window, ElementSelector(name="Questo elemento non esiste XYZ"))

        self.assertEqual(result, [])

    def test_combining_name_and_control_type_narrows_correctly(self):
        """'Tab 1' compare sia come Tab (il contenitore) sia come TabItem (la linguetta) nel dump
        F3.2 - il control_type e' indispensabile per scegliere quello giusto."""
        tab_item = self.engine.find_unique(self.window, ElementSelector(name="Tab 1", control_type="TabItem"))

        self.assertEqual(tab_item.control_type, "TabItem")
        self.assertTrue(tab_item.selected, "Tab 1 e' attiva per default")


if __name__ == "__main__":
    unittest.main()
