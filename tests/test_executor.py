"""Test unitari per core/computer_use/executor.py (F3.4.1/F3.4.4, prima fetta dell'executor
semantico - mai iniziata prima d'ora). Stesso schema di tests/test_ui_automation_adapter.py:
lancia davvero la fixture di F3.1.1 in un processo separato e agisce su di essa con UI Automation
vera, poi rilegge lo stato con l'adapter per verificare che l'azione abbia avuto un EFFETTO
reale, non solo che la chiamata non abbia sollevato un errore - la stessa distinzione che ha
trovato il buco reale documentato in ExpandCollapseKnownLimitationTests sotto."""
import subprocess
import sys
import time
import unittest
from pathlib import Path

from core.computer_use.executor import ActionExecutor, ElementActionReceipt, ElementNotInteractableError
from core.computer_use.ui_automation_adapter import UIAutomationAdapter, WindowNotFoundError
from tests.test_ui_automation_adapter import _RealFixtureTestCase

_SETTLE_SECONDS = 0.3
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_WINDOW_TITLE = "Jake Computer Use Fixture"



# Mouse e tastiera veri: mai input fuori dalla fixture (vedi tests/fixture_input_guard.py).
from tests.fixture_input_guard import install as setUpModule, uninstall as tearDownModule  # noqa: E402,F401

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


class RangeValueTests(_ExecutorFixtureTestCase):
    """F3.1.2 Task 14 (adozione) - il pattern RangeValue su un `QSlider` reale, MAI dichiarato
    dall'elenco originale di F3.4.1 (Invoke/Value/Toggle/SelectionItem/ExpandCollapse/Scroll/
    Window) ma aggiunto quando lo slider e' comparso nella fixture. A differenza di
    `ExpandCollapseKnownLimitationTests`/`ScrollKnownLimitationTests` sotto, QUESTO pattern
    funziona GIA' correttamente su Qt - verificato leggendo `CurrentValue` PRIMA/DOPO, non solo
    che `SetValue()` non sollevi."""

    def tearDown(self):
        self._reset_fixture()

    def _current_slider_value(self, element) -> float:
        from comtypes.gen import UIAutomationClient as UIA
        pattern = element.GetCurrentPattern(UIA.UIA_RangeValuePatternId).QueryInterface(UIA.IUIAutomationRangeValuePattern)
        return pattern.CurrentValue

    def test_setting_the_value_actually_changes_it(self):
        slider = self._element(automation_id="QApplication.jake_fixture_window.fixture_slider")
        self.assertEqual(self._current_slider_value(slider), 0.0, "stato iniziale atteso, altrimenti il test non proverebbe un vero cambiamento")

        self.executor.set_range_value(slider, 42.0)
        time.sleep(_SETTLE_SECONDS)

        slider_after = self._element(automation_id="QApplication.jake_fixture_window.fixture_slider")
        self.assertEqual(self._current_slider_value(slider_after), 42.0)

    def test_reset_returns_the_slider_to_zero(self):
        slider = self._element(automation_id="QApplication.jake_fixture_window.fixture_slider")
        self.executor.set_range_value(slider, 77.0)
        time.sleep(_SETTLE_SECONDS)

        self._reset_fixture()

        slider_after = self._element(automation_id="QApplication.jake_fixture_window.fixture_slider")
        self.assertEqual(self._current_slider_value(slider_after), 0.0)

    def test_a_button_does_not_support_range_value(self):
        add_button = self._element(name="Aggiungi", control_type="Button")

        with self.assertRaises(ElementNotInteractableError):
            self.executor.set_range_value(add_button, 10.0)


class RangeValueOnSpinBoxTests(_ExecutorFixtureTestCase):
    """F3.1.2 Task 17 (adozione) - lo STESSO pattern RangeValue, qui su un `QSpinBox` (`Tab 3`,
    la nuova scheda dedicata ai task futuri - vedi il docstring della fixture per il motivo:
    continuare ad allungare la colonna verticale principale rischiava di far uscire la finestra
    dallo schermo, un buco reale gia' trovato per Task 16). Verificato funzionare correttamente
    come per lo slider (Task 14), non assunto per analogia.

    **Buco reale trovato scrivendo QUESTO test, non ipotizzato**: l'automation_id dello spinbox e'
    un percorso QUALIFICATO che include l'INTERA catena di antenati (tab genitrice compresa,
    "...fixture_tabs.qt_tabwidget_stackedwidget.fixture_tab_three_content.fixture_spinbox") - lo
    stesso genere di sorpresa gia' documentato per il checkbox di Tab 2 (F3.2) - cercato per NOME
    invece, piu' semplice e comunque univoco qui. Un secondo buco, DIVERSO: il contenuto di una
    tab NON attiva non compare affatto nell'albero UI Automation finche' la tab non viene
    selezionata per davvero (`wait_for_unique_element`, non un singolo tentativo, per lasciare
    il tempo all'albero di "svegliarsi" dopo il cambio scheda)."""

    def tearDown(self):
        self._reset_fixture()

    def _current_spinbox_value(self, element) -> float:
        from comtypes.gen import UIAutomationClient as UIA
        pattern = element.GetCurrentPattern(UIA.UIA_RangeValuePatternId).QueryInterface(UIA.IUIAutomationRangeValuePattern)
        return pattern.CurrentValue

    def _spinbox_on_tab_three(self):
        tab_three = self._element(name="Tab 3", control_type="TabItem")
        self.executor.select(tab_three)
        time.sleep(_SETTLE_SECONDS)
        from core.computer_use.selector import ElementSelector, SelectorEngine
        engine = SelectorEngine(self.adapter)
        return engine.wait_for_unique_element(self.window, ElementSelector(name="Selettore numerico", control_type="Spinner"), timeout_seconds=3.0)

    def test_setting_the_value_actually_changes_it(self):
        spinbox = self._spinbox_on_tab_three()
        self.assertEqual(self._current_spinbox_value(spinbox), 0.0, "stato iniziale atteso, altrimenti il test non proverebbe un vero cambiamento")

        self.executor.set_range_value(spinbox, 55.0)
        time.sleep(_SETTLE_SECONDS)

        spinbox_after = self._spinbox_on_tab_three()
        self.assertEqual(self._current_spinbox_value(spinbox_after), 55.0)

    def test_reset_returns_the_spinbox_to_zero_and_the_active_tab_to_the_first(self):
        spinbox = self._spinbox_on_tab_three()
        self.executor.set_range_value(spinbox, 90.0)
        time.sleep(_SETTLE_SECONDS)

        self._reset_fixture()

        tab_one = self._element(name="Tab 1", control_type="TabItem")
        self.assertTrue(self.adapter.describe_element(tab_one).selected, "reset deve riportare anche l'interfaccia alla prima scheda")

        spinbox_after = self._spinbox_on_tab_three()
        self.assertEqual(self._current_spinbox_value(spinbox_after), 0.0)


class RadioButtonMutualExclusivityTests(_ExecutorFixtureTestCase):
    """F3.1.2 Task 18 (adozione) - un gruppo di `QRadioButton` (`Tab 3`) - il pattern "scegli
    esattamente una tra piu' opzioni mutuamente esclusive", diverso da una `QCheckBox` singola
    (F3.4.1, indipendente). Verificato con un probe dedicato PRIMA di scrivere questo test:
    `SelectionItem.Select()` su un radio button deseleziona DAVVERO gli altri del gruppo (la
    STESSA mutua esclusivita' affidabile gia' nota per un `TabItem`, non la trappola gia' nota per
    un `QListWidgetItem`) - un radio button espone SIA SelectionItem SIA Toggle, ma solo
    SelectionItem rispetta l'esclusivita' del gruppo."""

    def tearDown(self):
        self._reset_fixture()

    def _radio_on_tab_three(self, name: str):
        tab_three = self._element(name="Tab 3", control_type="TabItem")
        self.executor.select(tab_three)
        time.sleep(_SETTLE_SECONDS)
        from core.computer_use.selector import ElementSelector, SelectorEngine
        engine = SelectorEngine(self.adapter)
        return engine.wait_for_unique_element(self.window, ElementSelector(name=name, control_type="RadioButton"), timeout_seconds=3.0)

    def test_the_first_radio_is_checked_by_default(self):
        red = self._radio_on_tab_three("Rosso")
        green = self._radio_on_tab_three("Verde")
        self.assertTrue(self.adapter.describe_element(red).selected)
        self.assertFalse(self.adapter.describe_element(green).selected)

    def test_selecting_a_different_radio_really_deselects_the_previous_one(self):
        green = self._radio_on_tab_three("Verde")

        self.executor.select(green)
        time.sleep(_SETTLE_SECONDS)

        red_after = self._radio_on_tab_three("Rosso")
        green_after = self._radio_on_tab_three("Verde")
        self.assertFalse(self.adapter.describe_element(red_after).selected, "l'altro radio button del gruppo deve risultare DAVVERO deselezionato")
        self.assertTrue(self.adapter.describe_element(green_after).selected)

    def test_reset_returns_to_the_first_radio_selected(self):
        blue = self._radio_on_tab_three("Blu")
        self.executor.select(blue)
        time.sleep(_SETTLE_SECONDS)

        self._reset_fixture()

        red_after = self._radio_on_tab_three("Rosso")
        self.assertTrue(self.adapter.describe_element(red_after).selected)


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


class WindowPatternTests(unittest.TestCase):
    """F3.4.1 (resto): il pattern Window - `close_window()`, l'ultima azione tra i sette pattern
    dichiarati da F3.4.1 non ancora coperta. Un processo fixture DEDICATO per test (non quello
    condiviso di `_RealFixtureTestCase`/`_ExecutorFixtureTestCase`): chiudere la finestra e'
    un'azione irreversibile che romperebbe ogni altro test se condivisa con loro."""

    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        self.addCleanup(self._cleanup_process)
        self.adapter = UIAutomationAdapter()
        self.executor = ActionExecutor()
        self.window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)

    def _cleanup_process(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def test_closing_the_window_makes_it_really_disappear_and_the_process_really_exit(self):
        """A differenza di `ExpandCollapseKnownLimitationTests` sotto, qui NON c'e' un buco - il
        ponte di accessibilita' di Qt onora `Close()` davvero: la finestra sparisce dall'albero UI
        Automation E il processo termina da solo, non solo che `Close()` non abbia sollevato."""
        self.executor.close_window(self.window)

        with self.assertRaises(WindowNotFoundError):
            self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=2.0)
        exit_code = self.process.wait(timeout=5)
        self.assertIsNotNone(exit_code, "il processo deve terminare da solo, non solo la sua finestra sparire")

    def test_close_window_returns_a_receipt_naming_the_window_pattern(self):
        receipt = self.executor.close_window(self.window)
        self.process.wait(timeout=5)

        self.assertEqual(receipt.action, "close_window")

    def test_minimizing_then_restoring_really_changes_the_visual_state(self):
        """Task 28 di F3.1.2 (continua verso i 100) - il resto del pattern Window, MAI esercitato
        prima di questo incremento: `SetWindowVisualState` verso "minimizzata"/"normale",
        verificato leggendo `window_visual_state` via UI Automation dopo ognuna, non assunto."""
        self.assertEqual(self.adapter.window_visual_state(self.window), "normal")

        self.executor.minimize_window(self.window)
        self.assertEqual(self.adapter.window_visual_state(self.window), "minimized")

        self.executor.restore_window(self.window)
        self.assertEqual(self.adapter.window_visual_state(self.window), "normal")

    def test_minimize_and_restore_return_receipts_naming_the_window_pattern(self):
        minimize_receipt = self.executor.minimize_window(self.window)
        restore_receipt = self.executor.restore_window(self.window)

        self.assertEqual(minimize_receipt.action, "minimize_window")
        self.assertEqual(minimize_receipt.pattern, "Window")
        self.assertEqual(restore_receipt.action, "restore_window")
        self.assertEqual(restore_receipt.pattern, "Window")


class TransformPatternTests(unittest.TestCase):
    """F3.1.2 Task 51-53 (continua verso i 100) - il pattern Transform, un OTTAVO pattern MAI
    dichiarato/esercitato finora in questo progetto (oltre ai sette di F3.4.1). Stesso processo
    fixture dedicato di `WindowPatternTests` - ridimensionare/spostare la finestra e' un'azione
    che potrebbe interferire con altri test se condivisa."""

    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        self.addCleanup(self._cleanup_process)
        self.adapter = UIAutomationAdapter()
        self.executor = ActionExecutor()
        self.window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)

    def _cleanup_process(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def test_the_window_reports_it_can_move_and_resize_but_not_rotate(self):
        """Task 51."""
        from comtypes.gen import UIAutomationClient as UIA
        pattern = self.window.GetCurrentPattern(UIA.UIA_TransformPatternId).QueryInterface(UIA.IUIAutomationTransformPattern)

        self.assertTrue(pattern.CurrentCanMove)
        self.assertTrue(pattern.CurrentCanResize)
        self.assertFalse(pattern.CurrentCanRotate)

    def test_resizing_the_window_really_changes_its_width(self):
        """Task 52. **Verificato con un probe dedicato, non assunto**: la larghezza finale non
        coincide necessariamente con quella richiesta (clampata dai vincoli di layout Qt) - questo
        test verifica solo che CAMBI davvero, non un valore esatto."""
        before = self.adapter.describe_element(self.window).bounds

        self.executor.resize_window(self.window, before[2] + 50, before[3])

        after = self.adapter.describe_element(self.window).bounds
        self.assertGreater(after[2], before[2], "la larghezza deve aumentare davvero, non solo che Resize() non sollevi")

    def test_moving_the_window_really_changes_its_position(self):
        """Task 53. Stessa cautela di Task 52: verifica solo che la posizione cambi, non le
        coordinate esatte."""
        before = self.adapter.describe_element(self.window).bounds

        self.executor.move_window(self.window, before[0] + 20, before[1] + 10)

        after = self.adapter.describe_element(self.window).bounds
        self.assertNotEqual((after[0], after[1]), (before[0], before[1]), "la posizione deve cambiare davvero")

    def test_resize_and_move_return_receipts_naming_the_transform_pattern(self):
        before = self.adapter.describe_element(self.window).bounds

        resize_receipt = self.executor.resize_window(self.window, before[2] + 10, before[3])
        move_receipt = self.executor.move_window(self.window, before[0] + 5, before[1] + 5)

        self.assertEqual(resize_receipt.action, "resize_window")
        self.assertEqual(resize_receipt.pattern, "Transform")
        self.assertEqual(move_receipt.action, "move_window")
        self.assertEqual(move_receipt.pattern, "Transform")


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


class SetToggleStateUnitTests(unittest.TestCase):
    """`set_toggle_state` senza fixture: ogni azione e' seguita dalla rilettura dello stato, Invoke
    si prova solo dopo che Toggle non ha raggiunto l'obiettivo, e uno stato irraggiungibile solleva
    invece di ciclare all'infinito. (Il caso reale - QCheckBox a tre stati - e' in
    tests/test_computer_use_integration.py, Task 42.)"""

    def _executor(self, toggle_cycle, invoke_cycle, start="off"):
        from unittest import mock

        state = {"value": start}
        calls = []
        executor = ActionExecutor()

        def advance(cycle, name):
            def action(_element):
                calls.append(name)
                state["value"] = cycle[(cycle.index(state["value"]) + 1) % len(cycle)]
            return action

        executor.toggle = advance(toggle_cycle, "Toggle")
        executor.invoke = advance(invoke_cycle, "Invoke")
        patcher = mock.patch("core.computer_use.executor._element_identity", return_value=("Casella", "", "CheckBox"))
        patcher.start()
        self.addCleanup(patcher.stop)
        return executor, (lambda _element: state["value"]), calls

    def test_toggle_is_used_when_it_reaches_the_target(self):
        executor, read_state, calls = self._executor(["off", "on"], ["off", "indeterminate", "on"])
        receipt = executor.set_toggle_state(object(), "on", read_state)
        self.assertEqual((receipt.pattern, calls), ("Toggle", ["Toggle"]))

    def test_invoke_is_tried_when_toggle_only_alternates_on_and_off(self):
        executor, read_state, calls = self._executor(["off", "on"], ["off", "indeterminate", "on"])
        receipt = executor.set_toggle_state(object(), "indeterminate", read_state)
        self.assertEqual(receipt.pattern, "Invoke")
        self.assertEqual(read_state(None), "indeterminate")
        self.assertEqual(calls[-1], "Invoke")
        self.assertEqual(calls[:3], ["Toggle"] * 3, "Invoke solo dopo i cicli Toggle falliti")

    def test_an_already_reached_state_performs_no_action(self):
        executor, read_state, calls = self._executor(["off", "on"], ["off", "on"], start="on")
        self.assertEqual(executor.set_toggle_state(object(), "on", read_state).pattern, "none")
        self.assertEqual(calls, [])

    def test_an_unreachable_state_raises_after_a_bounded_number_of_actions(self):
        executor, read_state, calls = self._executor(["off", "on"], ["off", "on"])
        executor._wait_state = staticmethod(lambda element, read, target, timeout_s=0.6: read(element) == target)
        with self.assertRaises(ElementNotInteractableError):
            executor.set_toggle_state(object(), "indeterminate", read_state, max_cycles=3)
        self.assertEqual(len(calls), 6)

    def test_an_invalid_target_is_rejected(self):
        executor, read_state, calls = self._executor(["off", "on"], ["off", "on"])
        with self.assertRaises(ValueError):
            executor.set_toggle_state(object(), "maybe", read_state)


if __name__ == "__main__":
    unittest.main()
