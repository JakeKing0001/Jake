"""Test unitari per core/computer_use/executor.py (F3.4.1/F3.4.4, prima fetta dell'executor
semantico - mai iniziata prima d'ora). Stesso schema di tests/test_ui_automation_adapter.py:
lancia davvero la fixture di F3.1.1 in un processo separato e agisce su di essa con UI Automation
vera, poi rilegge lo stato con l'adapter per verificare che l'azione abbia avuto un EFFETTO
reale, non solo che la chiamata non abbia sollevato un errore - la stessa distinzione che ha
trovato il buco reale documentato in ExpandCollapseKnownLimitationTests sotto."""
import time
import unittest

from core.computer_use.executor import ActionExecutor, ElementNotInteractableError
from tests.test_ui_automation_adapter import _RealFixtureTestCase

_SETTLE_SECONDS = 0.3


class _ExecutorFixtureTestCase(_RealFixtureTestCase):
    def setUp(self):
        self.executor = ActionExecutor()

    def _reset_fixture(self):
        """I test di questa classe MUTANO davvero la fixture (a differenza di test_ui_automation_
        adapter.py/test_selector.py, read-only) - il bottone Reset esiste apposta (F3.1.3)."""
        reset_button = self.adapter.find_matching_elements(
            self.window, name="Reset", control_type="Button",
        )[0]
        self.executor.invoke(reset_button)
        time.sleep(_SETTLE_SECONDS)

    def _element(self, **criteria):
        matches = self.adapter.find_matching_elements(self.window, **criteria)
        self.assertEqual(len(matches), 1, f"atteso esattamente un elemento per {criteria}")
        return matches[0]


class InvokeAndSetValueTests(_ExecutorFixtureTestCase):
    """Task 1/10 di F3.1.2, ora guidato davvero da UI Automation invece che dal test Qt diretto
    di tests/test_computer_use_fixture.py - zero coordinate pixel."""

    def tearDown(self):
        self._reset_fixture()

    def test_setting_the_input_value_then_invoking_add_puts_the_item_in_the_list(self):
        input_field = self._element(automation_id="QApplication.jake_fixture_window.fixture_input")
        add_button = self._element(name="Aggiungi", control_type="Button")

        self.executor.set_value(input_field, "ciao da UI Automation")
        self.executor.invoke(add_button)
        time.sleep(_SETTLE_SECONDS)

        list_items = self.adapter.find_matching_elements(self.window, automation_id="QApplication.jake_fixture_window.fixture_list")
        # fixture_list e' il CONTENITORE - i suoi ListItem figli sono quello che conta.
        tree = self.adapter.describe_tree(list_items[0], max_depth=2)
        self.assertIn("ciao da UI Automation", [child.name for child in tree.children])

    def test_invoking_a_disabled_button_raises_instead_of_pretending_to_click(self):
        """F3.4.4 (precondizione "controllo enabled"): 'Rimuovi selezionato' e' disabilitato
        senza una selezione - lo stesso stato gia' verificato a livello Qt in F3.1.1, qui
        verificato che l'executor lo rispetti DAVVERO prima di agire, non solo che l'invocazione
        fallisca lato Qt dopo il fatto."""
        remove_button = self._element(name="Rimuovi selezionato", control_type="Button")

        with self.assertRaises(ElementNotInteractableError):
            self.executor.invoke(remove_button)


class SelectionItemTests(_ExecutorFixtureTestCase):
    """Task 4/10 di F3.1.2 (prima meta' - "cambia tab"): il pattern SelectionItem, verificato
    leggendo `CurrentIsSelected` prima e dopo, non solo che Select() non sollevi."""

    def tearDown(self):
        self._reset_fixture()

    def test_selecting_tab_two_actually_changes_which_tab_is_active(self):
        tab_one = self._element(name="Tab 1", control_type="TabItem")
        tab_two = self._element(name="Tab 2", control_type="TabItem")
        self.assertTrue(self.adapter.describe_element(tab_one).selected)
        self.assertFalse(self.adapter.describe_element(tab_two).selected)

        self.executor.select(tab_two)
        time.sleep(_SETTLE_SECONDS)

        tab_one_after = self._element(name="Tab 1", control_type="TabItem")
        tab_two_after = self._element(name="Tab 2", control_type="TabItem")
        self.assertFalse(self.adapter.describe_element(tab_one_after).selected)
        self.assertTrue(self.adapter.describe_element(tab_two_after).selected)


class ToggleTests(_ExecutorFixtureTestCase):
    """Task 4/10 di F3.1.2 (seconda meta' - "spunta l'opzione"): il pattern Toggle, verificato
    leggendo `CurrentToggleState` (F3.2.3, esteso in questo stesso incremento) prima e dopo -
    non solo che Toggle() non sollevi, la stessa rigorosita' che ha trovato il buco reale di
    ExpandCollapse sotto."""

    def tearDown(self):
        self._reset_fixture()

    def _checkbox_after_selecting_tab_two(self):
        tab_two = self._element(name="Tab 2", control_type="TabItem")
        self.executor.select(tab_two)
        time.sleep(_SETTLE_SECONDS)
        # L'automation_id e' un percorso QUALIFICATO che include la tab genitrice (F3.2, gia'
        # documentato) - trovato per nome invece, piu' semplice e comunque univoco qui.
        return self._element(name="Opzione", control_type="CheckBox")

    def test_toggling_the_checkbox_actually_changes_its_state(self):
        checkbox = self._checkbox_after_selecting_tab_two()
        self.assertEqual(self.adapter.describe_element(checkbox).toggle_state, "off")

        self.executor.toggle(checkbox)
        time.sleep(_SETTLE_SECONDS)

        checkbox_after = self._element(name="Opzione", control_type="CheckBox")
        self.assertEqual(self.adapter.describe_element(checkbox_after).toggle_state, "on")

    def test_toggling_twice_returns_to_the_original_state(self):
        checkbox = self._checkbox_after_selecting_tab_two()

        self.executor.toggle(checkbox)
        time.sleep(_SETTLE_SECONDS)
        checkbox_after_one = self._element(name="Opzione", control_type="CheckBox")
        self.executor.toggle(checkbox_after_one)
        time.sleep(_SETTLE_SECONDS)

        checkbox_after_two = self._element(name="Opzione", control_type="CheckBox")
        self.assertEqual(self.adapter.describe_element(checkbox_after_two).toggle_state, "off")

    def test_a_button_does_not_support_toggle(self):
        add_button = self._element(name="Aggiungi", control_type="Button")

        with self.assertRaises(ElementNotInteractableError):
            self.executor.toggle(add_button)


class ExpandCollapseKnownLimitationTests(_ExecutorFixtureTestCase):
    """**Documenta un buco reale, non lo nasconde**: il pattern ExpandCollapse e' presente su un
    `QTreeWidgetItem` (GetCurrentPattern lo trova, Expand()/Collapse() non sollevano mai), ma il
    ponte di accessibilita' di Qt non gli da' alcun effetto - CurrentExpandCollapseState resta
    invariato, verificato esplicitamente (vedi il docstring di core/computer_use/executor.py per
    i dettagli). Questi test codificano l'esatto comportamento oggi osservato (un canarino: se
    l'implementazione di Qt migliorasse in una versione futura di PySide6, questi test
    fallirebbero e andrebbero aggiornati, non un risultato silenzioso da ignorare)."""

    def test_expand_does_not_raise_on_a_real_tree_item(self):
        category_a = self._element(name="Categoria A", control_type="TreeItem")

        self.executor.expand(category_a)  # non deve sollevare

    def test_expand_does_not_actually_reveal_the_children_on_qt(self):
        category_a = self._element(name="Categoria A", control_type="TreeItem")

        self.executor.expand(category_a)
        time.sleep(_SETTLE_SECONDS)

        category_a_after = self._element(name="Categoria A", control_type="TreeItem")
        tree = self.adapter.describe_tree(category_a_after, max_depth=3)
        self.assertEqual(tree.children, (), "limite noto: Qt non espone i figli anche dopo Expand()")


class ScrollKnownLimitationTests(_ExecutorFixtureTestCase):
    """**Documenta lo STESSO buco reale di ExpandCollapseKnownLimitationTests sopra, con una
    manifestazione diversa e piu' onesta**: a differenza di ExpandCollapse (dichiarato disponibile
    da Qt ma senza effetto), il ponte di accessibilita' di Qt riporta correttamente
    `IsScrollPatternAvailable=False` per un `QListWidget` - `GetCurrentPattern` restituisce
    nessun pattern, quindi `scroll_to_bottom()` solleva `ElementNotInteractableError` invece di
    eseguire un'azione senza effetto. La conseguenza pratica e' la stessa: una riga fuori vista
    resta irraggiungibile via UI Automation."""

    def _scroll_list_container(self):
        """Buco reale trovato scrivendo questo test, non ipotizzato: Qt assegna lo STESSO
        automation_id sia al contenitore `QListWidget` sia ai suoi `ListItem` figli visibili (gia'
        osservato anche per `fixture_tree`/`TreeItem` in F3.2) - l'automation_id da solo non basta
        a isolare il contenitore, serve anche il control_type."""
        return self._element(automation_id="QApplication.jake_fixture_window.fixture_scroll_list", control_type="List")

    def test_scroll_to_bottom_raises_because_qt_does_not_support_the_pattern(self):
        scroll_list = self._scroll_list_container()

        with self.assertRaises(ElementNotInteractableError):
            self.executor.scroll_to_bottom(scroll_list)

    def test_scroll_to_top_raises_for_the_same_reason(self):
        scroll_list = self._scroll_list_container()

        with self.assertRaises(ElementNotInteractableError):
            self.executor.scroll_to_top(scroll_list)

    def test_a_row_scrolled_out_of_view_is_simply_absent_from_the_tree(self):
        """La ragione per cui il limite sopra conta davvero: 'Riga 30' esiste nel modello Qt (la
        lista ha 30 righe, F3.1.1) ma non e' raggiungibile ne' osservabile finche' qualcosa non
        la scorre in vista - e niente in UI Automation puo' farlo per questo widget."""
        matches = self.adapter.find_matching_elements(self.window, name="Riga 30")

        self.assertEqual(matches, [], "una riga fuori vista non deve comparire nell'albero UI Automation")


if __name__ == "__main__":
    unittest.main()
