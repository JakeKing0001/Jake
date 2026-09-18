"""Test unitari per core/computer_use/executor.py (F3.4.1/F3.4.4, prima fetta dell'executor
semantico - mai iniziata prima d'ora). Stesso schema di tests/test_ui_automation_adapter.py:
lancia davvero la fixture di F3.1.1 in un processo separato e agisce su di essa con UI Automation
vera, poi rilegge lo stato con l'adapter per verificare che l'azione abbia avuto un EFFETTO
reale, non solo che la chiamata non abbia sollevato un errore - la stessa distinzione che ha
trovato il buco reale documentato in ExpandCollapseKnownLimitationTests sotto."""
import time
import unittest

from core.computer_use.executor import ActionExecutor, ElementActionReceipt, ElementNotInteractableError
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
    """Task 4/10 di F3.1.2 (prima meta' - "cambia tab"): il pattern SelectionItem su un `TabItem`,
    verificato con una prova INDIPENDENTE dal solo `CurrentIsSelected` - il checkbox della tab 2
    (`ToggleTests._checkbox_after_selecting_tab_two` sotto lo riusa gia') diventa davvero
    raggiungibile via UI Automation solo se la tab e' VERAMENTE cambiata a livello Qt, non solo
    "selected" secondo UI Automation. Contrasto deliberato con
    `SelectionItemKnownLimitationTests` sotto, dove la STESSA prova indipendente su un
    `QListWidgetItem` smaschera l'esatto opposto."""

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


class SelectionItemKnownLimitationTests(_ExecutorFixtureTestCase):
    """**Il buco piu' insidioso dei tre trovati in questo incremento - una vera trappola, non
    ipotizzata**: a differenza di `SelectionItemTests` sopra (`TabItem`, verificato funzionante
    con una prova indipendente), su un `QListWidgetItem` `CurrentIsSelected` cambia correttamente
    da `False` a `True` dopo `select()` - SEMBREREBBE quindi funzionare - ma il bottone "Rimuovi
    selezionato" della fixture, la cui abilitazione dipende dal VERO stato di selezione di Qt
    (`itemSelectionChanged`, non da UI Automation), resta disabilitato. Lo stato riportato da UI
    Automation e quello reale dell'app si sono desincronizzati: fidarsi del solo `selected` come
    prova sarebbe stato un errore, scoperto qui SOLO perche' esisteva un secondo modo indipendente
    di controllare - la stessa lezione "mai fidarsi del successo auto-dichiarato, verificarlo in
    modo indipendente" gia' imparata ripetutamente per le skill di Jake in F1
    (`core/execution_safety.py::verify_effect`), qui riscoperta per UI Automation stesso."""

    def tearDown(self):
        self._reset_fixture()

    def test_selecting_a_list_item_reports_selected_but_does_not_really_select_it(self):
        item = self._element(name="Riga 1", control_type="ListItem")
        self.assertFalse(self.adapter.describe_element(item).selected)

        self.executor.select(item)
        time.sleep(_SETTLE_SECONDS)

        item_after = self._element(name="Riga 1", control_type="ListItem")
        self.assertTrue(
            self.adapter.describe_element(item_after).selected,
            "UI Automation SI' riporta la selezione come riuscita - questo e' il punto della trappola",
        )
        remove_button = self._element(name="Rimuovi selezionato", control_type="Button")
        self.assertFalse(
            self.adapter.describe_element(remove_button).enabled,
            "ma Qt non ha davvero selezionato nulla - il bottone che dipende dalla selezione VERA resta disabilitato",
        )


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


class ActionReceiptTests(_ExecutorFixtureTestCase):
    """F3.4.5: ogni metodo pubblico restituisce ora un `ElementActionReceipt` invece di `None` -
    CHI ha agito (l'elemento) e CON QUALE pattern, non che l'azione abbia avuto un effetto reale
    (quello resta una responsabilita' del chiamante, vedi il docstring del modulo per la
    trappola di SelectionItem che si applica identica qui)."""

    def tearDown(self):
        self._reset_fixture()

    def test_invoke_returns_a_receipt_naming_the_invoke_pattern_and_the_real_element(self):
        add_button = self._element(name="Aggiungi", control_type="Button")

        receipt = self.executor.invoke(add_button)

        self.assertIsInstance(receipt, ElementActionReceipt)
        self.assertEqual(receipt.action, "invoke")
        self.assertEqual(receipt.pattern, "Invoke")
        self.assertEqual(receipt.element_name, "Aggiungi")
        self.assertEqual(receipt.element_control_type, "Button")
        self.assertTrue(receipt.element_automation_id.endswith("fixture_add_button"))

    def test_set_value_returns_a_receipt_for_the_value_pattern_without_leaking_the_text(self):
        input_field = self._element(automation_id="QApplication.jake_fixture_window.fixture_input")

        receipt = self.executor.set_value(input_field, "un segreto qualunque")

        self.assertEqual(receipt.action, "set_value")
        self.assertEqual(receipt.pattern, "Value")
        self.assertNotIn("un segreto qualunque", str(receipt))

    def test_the_receipt_timestamp_is_a_real_recent_wall_clock_time(self):
        add_button = self._element(name="Aggiungi", control_type="Button")
        before = time.time()

        receipt = self.executor.invoke(add_button)

        self.assertGreaterEqual(receipt.ts, before)
        self.assertLessEqual(receipt.ts, time.time())

    def test_a_failed_precondition_raises_instead_of_returning_a_false_receipt(self):
        """Nessuna ricevuta con un campo 'riuscito=False' inventato - il fallimento resta
        un'eccezione, esattamente come prima di questo incremento (F3.4.4)."""
        remove_button = self._element(name="Rimuovi selezionato", control_type="Button")

        with self.assertRaises(ElementNotInteractableError):
            self.executor.invoke(remove_button)

    def test_toggle_returns_a_receipt_for_the_toggle_pattern(self):
        tab_two = self._element(name="Tab 2", control_type="TabItem")
        self.executor.select(tab_two)
        time.sleep(_SETTLE_SECONDS)
        checkbox = self._element(name="Opzione", control_type="CheckBox")

        receipt = self.executor.toggle(checkbox)

        self.assertEqual(receipt.pattern, "Toggle")
        self.assertEqual(receipt.element_name, "Opzione")


if __name__ == "__main__":
    unittest.main()
