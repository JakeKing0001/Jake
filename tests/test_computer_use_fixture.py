"""Test unitari per benchmarks/computer_use_fixture.py (F3.1.1, prima fetta della fase Computer
Use Engine 3.0 - "Stato: READY, mai iniziato" prima di questo incremento). Stesso schema di
tests/test_hud_widgets.py per i widget Qt esistenti: una QApplication reale (Qt lo richiede per
costruire i widget), mai una vera finestra mostrata (show()), click simulati con QTest.mouseClick
(un evento Qt vero, non una chiamata diretta allo slot) per provare che il controllo sia davvero
cliccabile, non solo che il suo metodo funzioni se chiamato a mano."""
import unittest

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from benchmarks.computer_use_fixture import ComputerUseFixtureWindow

_app = QApplication.instance() or QApplication([])


def _add(window: ComputerUseFixtureWindow, text: str) -> None:
    window.input_field.setText(text)
    QTest.mouseClick(window.add_button, Qt.LeftButton)


class AutomationPropertiesTests(unittest.TestCase):
    """F3.2.3 ("esporre role, name, automation id...") dipende da questi nomi essere impostati
    ESPLICITAMENTE, non lasciati al default Qt (vuoto/non stabile) - verificato qui perche' un
    domani F3.2 li dia per scontati senza aver mai controllato che esistano davvero."""

    def test_every_control_has_a_stable_object_name(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.objectName(), "jake_fixture_window")
        self.assertEqual(window.input_field.objectName(), "fixture_input")
        self.assertEqual(window.add_button.objectName(), "fixture_add_button")
        self.assertEqual(window.reset_button.objectName(), "fixture_reset_button")
        self.assertEqual(window.item_list.objectName(), "fixture_list")
        self.assertEqual(window.tree.objectName(), "fixture_tree")
        self.assertEqual(window.tabs.objectName(), "fixture_tabs")
        self.assertEqual(window.option_checkbox.objectName(), "fixture_checkbox")
        self.assertEqual(window.scroll_list.objectName(), "fixture_scroll_list")
        self.assertEqual(window.load_button.objectName(), "fixture_load_button")
        self.assertEqual(window.dynamic_button.objectName(), "fixture_dynamic_button")
        self.assertEqual(window.action_button_a.objectName(), "fixture_action_a")
        self.assertEqual(window.action_button_b.objectName(), "fixture_action_b")
        self.assertEqual(window.option_combo.objectName(), "fixture_combo")
        self.assertEqual(window.value_slider.objectName(), "fixture_slider")
        self.assertEqual(window.progress_bar.objectName(), "fixture_progress")
        self.assertEqual(window.start_progress_button.objectName(), "fixture_start_progress_button")
        self.assertEqual(window.reorder_list.objectName(), "fixture_reorder_list")
        self.assertEqual(window.value_spinbox.objectName(), "fixture_spinbox")
        self.assertEqual(window.radio_red.objectName(), "fixture_radio_red")
        self.assertEqual(window.radio_green.objectName(), "fixture_radio_green")
        self.assertEqual(window.radio_blue.objectName(), "fixture_radio_blue")
        self.assertEqual(window.data_table.objectName(), "fixture_table")
        self.assertEqual(window.transfer_source_list.objectName(), "fixture_transfer_source")
        self.assertEqual(window.transfer_target_list.objectName(), "fixture_transfer_target")
        self.assertEqual(window.date_edit.objectName(), "fixture_date_edit")
        self.assertEqual(window.filter_input.objectName(), "fixture_filter_input")
        self.assertEqual(window.filter_list.objectName(), "fixture_filter_list")
        self.assertEqual(window.multiline_edit.objectName(), "fixture_multiline_edit")

    def test_every_control_has_a_non_empty_accessible_name(self):
        window = ComputerUseFixtureWindow()
        for widget in (
            window.input_field, window.add_button, window.reset_button, window.item_list, window.tree,
            window.tabs, window.option_checkbox, window.scroll_list, window.load_button, window.dynamic_button,
            window.action_button_a, window.action_button_b, window.option_combo, window.value_slider,
            window.value_spinbox, window.radio_red, window.radio_green, window.radio_blue,
            window.progress_bar, window.start_progress_button, window.reorder_list, window.data_table,
            window.transfer_source_list, window.transfer_target_list, window.date_edit,
            window.filter_input, window.filter_list, window.multiline_edit,
        ):
            self.assertTrue(widget.accessibleName(), f"{widget.objectName()} non ha un accessibleName")

    def test_the_window_title_is_stable(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.windowTitle(), "Jake Computer Use Fixture")


class InitialStateTests(unittest.TestCase):
    def test_a_fresh_window_starts_with_an_empty_list_and_input(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.list_items(), [])
        self.assertEqual(window.input_field.text(), "")


class AddItemTaskTests(unittest.TestCase):
    """Task 1/10 di F3.1.2 (gli altri 9 restano un passo successivo dichiarato): digitare un
    testo e cliccare 'Aggiungi' deve farlo comparire nella lista - il click e' simulato con
    QTest.mouseClick (un evento Qt reale sul bottone), non una chiamata diretta al metodo."""

    def test_clicking_add_with_text_moves_it_into_the_list(self):
        window = ComputerUseFixtureWindow()
        window.input_field.setText("ciao")

        QTest.mouseClick(window.add_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), ["ciao"])

    def test_clicking_add_clears_the_input_field(self):
        window = ComputerUseFixtureWindow()
        window.input_field.setText("ciao")

        QTest.mouseClick(window.add_button, Qt.LeftButton)

        self.assertEqual(window.input_field.text(), "")

    def test_clicking_add_with_an_empty_field_adds_nothing(self):
        window = ComputerUseFixtureWindow()

        QTest.mouseClick(window.add_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), [])

    def test_clicking_add_with_only_whitespace_adds_nothing(self):
        window = ComputerUseFixtureWindow()
        window.input_field.setText("   ")

        QTest.mouseClick(window.add_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), [])

    def test_adding_twice_accumulates_both_items_in_order(self):
        window = ComputerUseFixtureWindow()

        window.input_field.setText("primo")
        QTest.mouseClick(window.add_button, Qt.LeftButton)
        window.input_field.setText("secondo")
        QTest.mouseClick(window.add_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), ["primo", "secondo"])


class ResetStateTests(unittest.TestCase):
    """F3.1.3: 'resettare lo stato della fixture prima di ogni task' - un task non deve mai
    ereditare residui lasciati da quello precedente."""

    def test_clicking_reset_clears_a_populated_list_and_input(self):
        window = ComputerUseFixtureWindow()
        window.input_field.setText("ciao")
        QTest.mouseClick(window.add_button, Qt.LeftButton)
        window.input_field.setText("residuo non ancora aggiunto")

        QTest.mouseClick(window.reset_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), [])
        self.assertEqual(window.input_field.text(), "")

    def test_reset_on_an_already_empty_window_does_not_raise(self):
        window = ComputerUseFixtureWindow()
        window.reset_state()
        self.assertEqual(window.list_items(), [])

    def test_reset_disables_the_remove_button_again(self):
        window = ComputerUseFixtureWindow()
        _add(window, "ciao")
        window.item_list.setCurrentRow(0)
        self.assertTrue(window.remove_button.isEnabled())

        QTest.mouseClick(window.reset_button, Qt.LeftButton)

        self.assertFalse(window.remove_button.isEnabled())

    def test_reset_disables_the_dynamic_button_again_even_mid_timer(self):
        """Reset deve funzionare anche se chiamato PRIMA che il timer sia scattato (un task
        interrotto a meta') - il bottone deve restare/tornare disabilitato, non solo essere
        disabilitato quando il reset arriva dopo che il timer e' gia' scattato.

        **Buco reale trovato scrivendo questo test, non ipotizzato**: una prima versione della
        fixture usava `QTimer.singleShot` (la comodita' statica, senza un riferimento a cui
        chiedere `.stop()`) - il reset azzerava lo stato VISIBILE subito, ma il timer GIA'
        schedulato al click su "Carica dati" continuava comunque, riabilitando il bottone da
        solo circa 1s dopo, vanificando il reset in silenzio. Il controllo SUBITO dopo il reset
        (sotto) non l'avrebbe mai scoperto - serve aspettare DAVVERO oltre la durata del timer
        originale (`QTest.qWait`, non un singolo controllo immediato) per una prova vera."""
        window = ComputerUseFixtureWindow()
        QTest.mouseClick(window.load_button, Qt.LeftButton)

        QTest.mouseClick(window.reset_button, Qt.LeftButton)

        self.assertFalse(window.dynamic_button.isEnabled())
        self.assertFalse(window.dynamic_action_activated)

        QTest.qWait(1200)  # oltre l'intero secondo del timer originale, se mai fosse sopravvissuto al reset
        self.assertFalse(window.dynamic_button.isEnabled(), "il timer originale non deve MAI riabilitarlo dopo un reset")


class RemoveButtonEnabledStateTests(unittest.TestCase):
    """F3.1.6 (assaggio anticipato - "controlli disabilitati"): il bottone 'Rimuovi selezionato'
    e' disabilitato per davvero senza una selezione, non solo visivamente - Qt non emette
    clicked() su un QPushButton disabilitato, verificato qui provando davvero a cliccarlo."""

    def test_the_button_starts_disabled_on_a_fresh_window(self):
        window = ComputerUseFixtureWindow()
        self.assertFalse(window.remove_button.isEnabled())

    def test_selecting_an_item_enables_the_button(self):
        window = ComputerUseFixtureWindow()
        _add(window, "ciao")

        window.item_list.setCurrentRow(0)

        self.assertTrue(window.remove_button.isEnabled())

    def test_clicking_a_disabled_remove_button_does_nothing(self):
        window = ComputerUseFixtureWindow()
        _add(window, "ciao")

        QTest.mouseClick(window.remove_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), ["ciao"], "senza selezione, il click non deve avere alcun effetto")


class DynamicButtonTests(unittest.TestCase):
    """Task 6/10 di F3.1.2 (F3.1.6, la parte DINAMICA - a differenza di
    `RemoveButtonEnabledStateTests` sopra, dove il bottone cambia stato in modo SINCRONO dentro lo
    stesso gestore di click che lo scopre, qui il cambiamento avviene DOPO, tramite un vero
    `QTimer` - lo stesso genere di attesa che un'app reale impone per un'operazione asincrona)."""

    def test_the_button_starts_disabled_on_a_fresh_window(self):
        window = ComputerUseFixtureWindow()
        self.assertFalse(window.dynamic_button.isEnabled())

    def test_clicking_load_does_not_enable_it_immediately(self):
        window = ComputerUseFixtureWindow()

        QTest.mouseClick(window.load_button, Qt.LeftButton)

        self.assertFalse(window.dynamic_button.isEnabled(), "non deve essere gia' abilitato subito dopo il click, prima che il timer scada")

    def test_clicking_load_enables_it_only_after_the_timer_really_fires(self):
        window = ComputerUseFixtureWindow()

        QTest.mouseClick(window.load_button, Qt.LeftButton)
        QTest.qWait(1200)  # oltre il secondo dichiarato dal timer, non un'attesa arbitraria

        self.assertTrue(window.dynamic_button.isEnabled())

    def test_clicking_a_disabled_dynamic_button_does_nothing(self):
        window = ComputerUseFixtureWindow()

        QTest.mouseClick(window.dynamic_button, Qt.LeftButton)

        self.assertFalse(window.dynamic_action_activated, "un bottone disabilitato non deve MAI emettere clicked()")

    def test_clicking_it_once_really_enabled_activates_the_dynamic_action(self):
        window = ComputerUseFixtureWindow()
        QTest.mouseClick(window.load_button, Qt.LeftButton)
        QTest.qWait(1200)

        QTest.mouseClick(window.dynamic_button, Qt.LeftButton)

        self.assertTrue(window.dynamic_action_activated)

    def test_clicking_load_twice_restarts_the_timer_from_the_second_click(self):
        """`.start()` (non `QTimer.singleShot`) riavvia il conto alla rovescia da capo se
        cliccato una seconda volta - verificato che il bottone resti DISABILITATO subito dopo un
        secondo click (il timer e' ripartito da zero), non che sia rimasto abilitato dal primo
        tentativo (che qui non ha nemmeno avuto il tempo di scadere)."""
        window = ComputerUseFixtureWindow()
        QTest.mouseClick(window.load_button, Qt.LeftButton)
        QTest.qWait(700)  # meno del secondo dichiarato: il primo timer non e' ancora scaduto

        QTest.mouseClick(window.load_button, Qt.LeftButton)

        self.assertFalse(window.dynamic_button.isEnabled(), "il secondo click deve far ripartire il timer da capo")


class AmbiguousButtonsTests(unittest.TestCase):
    """Task 7/10 di F3.1.2 (F3.1.6, RESTO - "controlli ambigui", il bersaglio DEDICATO che
    mancava in questa fixture). `action_button_a`/`action_button_b` condividono lo STESSO
    accessibleName ("Azione") - una UI Automation reale (F3.2+) che cercasse solo per nome
    troverebbe entrambi, ambiguamente; qui a livello Qt si verifica solo che i DUE contatori
    restino davvero INDIPENDENTI, la base su cui un test end-to-end futuro potra' dimostrare la
    disambiguazione per automation_id."""

    def test_both_buttons_share_the_same_accessible_name(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.action_button_a.accessibleName(), "Azione")
        self.assertEqual(window.action_button_b.accessibleName(), "Azione")

    def test_the_two_buttons_have_different_object_names(self):
        window = ComputerUseFixtureWindow()
        self.assertNotEqual(window.action_button_a.objectName(), window.action_button_b.objectName())

    def test_clicking_button_a_only_increments_its_own_counter(self):
        window = ComputerUseFixtureWindow()

        QTest.mouseClick(window.action_button_a, Qt.LeftButton)

        self.assertEqual(window.action_a_clicks, 1)
        self.assertEqual(window.action_b_clicks, 0)

    def test_clicking_button_b_only_increments_its_own_counter(self):
        window = ComputerUseFixtureWindow()

        QTest.mouseClick(window.action_button_b, Qt.LeftButton)

        self.assertEqual(window.action_a_clicks, 0)
        self.assertEqual(window.action_b_clicks, 1)

    def test_reset_clears_both_counters(self):
        window = ComputerUseFixtureWindow()
        QTest.mouseClick(window.action_button_a, Qt.LeftButton)
        QTest.mouseClick(window.action_button_b, Qt.LeftButton)

        QTest.mouseClick(window.reset_button, Qt.LeftButton)

        self.assertEqual(window.action_a_clicks, 0)
        self.assertEqual(window.action_b_clicks, 0)


class ConfirmationDialogAutomationPropertiesTests(unittest.TestCase):
    """F3.2.3 ("esporre role, name, automation id...") per il dialogo modale, non solo per i
    controlli della finestra principale - stesso principio, stessa verifica esplicita."""

    def test_the_dialog_and_its_two_buttons_have_stable_names(self):
        window = ComputerUseFixtureWindow()

        dialog = window._build_confirmation_dialog("prova")

        self.assertEqual(dialog.objectName(), "fixture_confirm_dialog")
        yes_button = dialog.findChild(QPushButton, "fixture_confirm_yes")
        no_button = dialog.findChild(QPushButton, "fixture_confirm_no")
        self.assertIsNotNone(yes_button)
        self.assertIsNotNone(no_button)
        self.assertEqual(yes_button.accessibleName(), "Sì")
        self.assertEqual(no_button.accessibleName(), "No")

    def test_the_dialog_text_names_the_item_to_remove(self):
        window = ComputerUseFixtureWindow()

        dialog = window._build_confirmation_dialog("il mio elemento")

        self.assertIn("il mio elemento", dialog.text())

    def test_no_is_the_default_button(self):
        """Un default sicuro: se l'utente premesse solo Invio senza guardare, non deve mai
        cancellare nulla per errore - stesso principio 'nessun effetto distruttivo per default'
        gia' seguito nel resto del progetto (es. DeletePathSkill, che richiede sempre conferma)."""
        window = ComputerUseFixtureWindow()

        dialog = window._build_confirmation_dialog("prova")

        self.assertEqual(dialog.defaultButton().objectName(), "fixture_confirm_no")


class RemoveWithConfirmationTests(unittest.TestCase):
    """Task 2/10 di F3.1.2. Il dialogo e' VERAMENTE modale (QMessageBox.exec() blocca in una
    propria coda di eventi) - il bottone di conferma viene cliccato da un QTimer.singleShot(0, ..)
    schedulato PRIMA di aprire il dialogo, lo stesso idioma standard per testare un modale Qt
    senza bloccare il test per sempre."""

    def _click_dialog_button(self, window: ComputerUseFixtureWindow, object_name: str) -> None:
        dialog = window.findChild(QMessageBox, "fixture_confirm_dialog")
        self.assertIsNotNone(dialog, "il dialogo doveva essere gia' aperto quando il timer e' scattato")
        button = dialog.findChild(QPushButton, object_name)
        QTest.mouseClick(button, Qt.LeftButton)

    def test_confirming_yes_removes_the_item(self):
        window = ComputerUseFixtureWindow()
        _add(window, "ciao")
        window.item_list.setCurrentRow(0)
        QTimer.singleShot(0, lambda: self._click_dialog_button(window, "fixture_confirm_yes"))

        QTest.mouseClick(window.remove_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), [])

    def test_answering_no_leaves_the_item_in_place(self):
        window = ComputerUseFixtureWindow()
        _add(window, "ciao")
        window.item_list.setCurrentRow(0)
        QTimer.singleShot(0, lambda: self._click_dialog_button(window, "fixture_confirm_no"))

        QTest.mouseClick(window.remove_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), ["ciao"])

    def test_confirming_yes_only_removes_the_selected_item_not_others(self):
        window = ComputerUseFixtureWindow()
        _add(window, "primo")
        _add(window, "secondo")
        window.item_list.setCurrentRow(0)
        QTimer.singleShot(0, lambda: self._click_dialog_button(window, "fixture_confirm_yes"))

        QTest.mouseClick(window.remove_button, Qt.LeftButton)

        self.assertEqual(window.list_items(), ["secondo"])


class TreeInitialStateTests(unittest.TestCase):
    """F3.4.1 (ExpandCollapse/Selection pattern): un albero a due livelli, TUTTE le categorie
    collassate per default e nessuna selezione - lo stesso stato ogni volta che la fixture
    riparte, non un'assunzione sul comportamento di default di Qt (verificato, non presunto)."""

    def test_both_categories_start_collapsed(self):
        window = ComputerUseFixtureWindow()
        self.assertFalse(window.is_category_expanded("Categoria A"))
        self.assertFalse(window.is_category_expanded("Categoria B"))

    def test_nothing_is_selected_on_a_fresh_window(self):
        window = ComputerUseFixtureWindow()
        self.assertIsNone(window.selected_tree_item_text())

    def test_an_unknown_category_name_raises_instead_of_silently_returning_false(self):
        window = ComputerUseFixtureWindow()
        with self.assertRaises(ValueError):
            window.is_category_expanded("Categoria inesistente")


class TreeExpandAndSelectTests(unittest.TestCase):
    """Task 3/10 di F3.1.2: espandere 'Categoria A' e selezionare 'Elemento A1' - un compito che
    richiede DAVVERO il pattern ExpandCollapse (il figlio non e' selezionabile/visibile finche'
    il genitore resta collassato), non solo Selection da solo come per la lista."""

    def _child_item(self, window: ComputerUseFixtureWindow, category_name: str, child_name: str):
        for i in range(window.tree.topLevelItemCount()):
            category = window.tree.topLevelItem(i)
            if category.text(0) != category_name:
                continue
            for j in range(category.childCount()):
                child = category.child(j)
                if child.text(0) == child_name:
                    return child
        raise AssertionError(f"{child_name} non trovato sotto {category_name}")

    def test_expanding_one_category_does_not_expand_the_other(self):
        window = ComputerUseFixtureWindow()

        window.tree.topLevelItem(0).setExpanded(True)

        self.assertTrue(window.is_category_expanded("Categoria A"))
        self.assertFalse(window.is_category_expanded("Categoria B"))

    def test_selecting_a_child_after_expanding_its_category(self):
        window = ComputerUseFixtureWindow()
        window.tree.topLevelItem(0).setExpanded(True)
        child = self._child_item(window, "Categoria A", "Elemento A1")

        window.tree.setCurrentItem(child)

        self.assertEqual(window.selected_tree_item_text(), "Elemento A1")

    def test_reset_collapses_categories_and_clears_the_selection(self):
        window = ComputerUseFixtureWindow()
        window.tree.topLevelItem(0).setExpanded(True)
        window.tree.setCurrentItem(self._child_item(window, "Categoria A", "Elemento A1"))

        window.reset_state()

        self.assertIsNone(window.selected_tree_item_text())
        self.assertFalse(window.is_category_expanded("Categoria A"))


class TabsInitialStateTests(unittest.TestCase):
    def test_tab_one_is_active_by_default(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.current_tab_name(), "Tab 1")

    def test_the_option_is_unchecked_by_default(self):
        window = ComputerUseFixtureWindow()
        self.assertFalse(window.is_option_checked())


class SwitchTabAndToggleTests(unittest.TestCase):
    """Task 4/10 di F3.1.2: cambiare tab e spuntare l'opzione - il pattern Toggle (F3.4.1), senza
    ancora un bersaglio nella fixture prima di questa fetta. Il click sulla casella e' simulato
    con QTest.mouseClick (un evento Qt vero), il cambio tab con l'API diretta di QTabWidget (lo
    stesso stato che un click reale sulla barra delle tab produrrebbe - QTest.mouseClick su una
    QTabBar richiederebbe coordinate pixel che dipendono dal layout, non ancora affrontato qui,
    stesso limite gia' dichiarato per le osservazioni "in processo" del resto della fixture)."""

    def test_switching_to_tab_two_updates_the_current_tab_name(self):
        window = ComputerUseFixtureWindow()

        window.tabs.setCurrentIndex(1)

        self.assertEqual(window.current_tab_name(), "Tab 2")

    def test_checking_the_option_on_tab_two(self):
        window = ComputerUseFixtureWindow()
        window.tabs.setCurrentIndex(1)

        QTest.mouseClick(window.option_checkbox, Qt.LeftButton)

        self.assertTrue(window.is_option_checked())

    def test_clicking_the_checkbox_twice_unchecks_it_again(self):
        window = ComputerUseFixtureWindow()
        window.tabs.setCurrentIndex(1)

        QTest.mouseClick(window.option_checkbox, Qt.LeftButton)
        QTest.mouseClick(window.option_checkbox, Qt.LeftButton)

        self.assertFalse(window.is_option_checked())

    def test_reset_returns_to_tab_one_and_unchecks_the_option(self):
        window = ComputerUseFixtureWindow()
        window.tabs.setCurrentIndex(1)
        QTest.mouseClick(window.option_checkbox, Qt.LeftButton)

        window.reset_state()

        self.assertEqual(window.current_tab_name(), "Tab 1")
        self.assertFalse(window.is_option_checked())


class ScrollListTests(unittest.TestCase):
    """Task 5/10 di F3.1.2: scorrere fino in fondo e selezionare l'ultima riga - il pattern
    Scroll (F3.4.1). A DIFFERENZA di ogni altra classe in questo file, qui la finestra viene
    davvero mostrata (`show()`): buco empirico trovato scrivendo questi test, non ipotizzato -
    `QScrollBar.maximum()` resta 0 finche' il widget non ha una geometria vera assegnata da un
    vero layout pass, che Qt non esegue mai per un widget mai mostrato (verificato: `resize()`/
    `adjustSize()`/`processEvents()` senza `show()` non bastano, `maximum()` resta 0 comunque).
    Senza mostrare la finestra, `is_scrolled_to_bottom()` sarebbe banalmente sempre vero
    (`0 >= 0`), un test che passa senza aver provato nulla. `addCleanup(window.close)` per non
    lasciare finestre aperte tra un test e l'altro."""

    def _shown_window(self) -> ComputerUseFixtureWindow:
        window = ComputerUseFixtureWindow()
        window.show()
        self.addCleanup(window.close)
        _app.processEvents()
        return window

    def test_a_fresh_window_is_not_scrolled_to_the_bottom(self):
        window = self._shown_window()
        self.assertFalse(window.is_scrolled_to_bottom())

    def test_nothing_is_selected_on_a_fresh_window(self):
        window = self._shown_window()
        self.assertIsNone(window.selected_scroll_item_text())

    def test_scrolling_to_the_bottom_and_selecting_the_last_row(self):
        window = self._shown_window()

        window.scroll_list.scrollToBottom()
        _app.processEvents()
        last_row = window.scroll_list.item(window.scroll_list.count() - 1)
        window.scroll_list.setCurrentItem(last_row)

        self.assertTrue(window.is_scrolled_to_bottom())
        self.assertEqual(window.selected_scroll_item_text(), "Riga 30")

    def test_reset_scrolls_back_to_the_top_and_clears_the_selection(self):
        window = self._shown_window()
        window.scroll_list.scrollToBottom()
        _app.processEvents()
        window.scroll_list.setCurrentItem(window.scroll_list.item(window.scroll_list.count() - 1))

        window.reset_state()

        self.assertFalse(window.is_scrolled_to_bottom())
        self.assertIsNone(window.selected_scroll_item_text())


class ComboBoxTests(unittest.TestCase):
    """Task 12 di F3.1.2 (continua verso i 100): `option_combo`, un terzo genere di controllo a
    selezione mai presente in questa fixture (diverso da lista e albero) - vedi
    `tests/test_computer_use_integration.py::ComboBoxSelectionEndToEndTests` per la dimostrazione
    end-to-end via UI Automation (che ha gia' trovato il buco reale: selezionare un'opzione dal
    popup richiede un click pixel, un `Invoke()` UIA non ha effetto)."""

    def test_the_first_option_is_selected_by_default(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.current_combo_option(), "Opzione 1")

    def test_all_three_options_are_present_in_order(self):
        window = ComputerUseFixtureWindow()
        options = [window.option_combo.itemText(i) for i in range(window.option_combo.count())]
        self.assertEqual(options, ["Opzione 1", "Opzione 2", "Opzione 3"])

    def test_reset_returns_the_combo_to_the_first_option(self):
        window = ComputerUseFixtureWindow()
        window.option_combo.setCurrentIndex(2)
        self.assertEqual(window.current_combo_option(), "Opzione 3")

        window.reset_state()

        self.assertEqual(window.current_combo_option(), "Opzione 1")


class SliderTests(unittest.TestCase):
    """Task 14 di F3.1.2 (continua verso i 100): `value_slider` (`QSlider`) - vedi
    `tests/test_executor.py::RangeValueTests` per la dimostrazione end-to-end via UI Automation
    (il pattern RangeValue funziona gia' correttamente, a differenza di quelli documentati come
    limitati altrove in questa fixture)."""

    def test_the_value_starts_at_zero(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.value_slider.value(), 0)

    def test_the_range_is_zero_to_one_hundred(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.value_slider.minimum(), 0)
        self.assertEqual(window.value_slider.maximum(), 100)

    def test_reset_returns_the_slider_to_zero(self):
        window = ComputerUseFixtureWindow()
        window.value_slider.setValue(55)
        self.assertEqual(window.value_slider.value(), 55)

        window.reset_state()

        self.assertEqual(window.value_slider.value(), 0)


class ProgressBarTests(unittest.TestCase):
    """Task 15 di F3.1.2 (continua verso i 100): `progress_bar`, riempita da un `QTimer`
    ricorrente REALE (non un salto istantaneo) - lo scenario motivante e' "aspetta che
    un'operazione lunga raggiunga il 100%", dove il VALORE intermedio stesso e' il segnale da
    osservare. Vedi `tests/test_computer_use_integration.py::ProgressBarEndToEndTests` per la
    dimostrazione via UI Automation (RangeValue, gia' verificato funzionante per Task 14)."""

    def test_the_value_starts_at_zero(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.progress_bar.value(), 0)

    def test_clicking_start_does_not_jump_to_completion_immediately(self):
        window = ComputerUseFixtureWindow()

        QTest.mouseClick(window.start_progress_button, Qt.LeftButton)

        self.assertEqual(window.progress_bar.value(), 0, "non deve gia' essere avanzata subito dopo il click, prima che il timer scatti")

    def test_the_bar_reaches_one_hundred_after_enough_real_time_passes(self):
        window = ComputerUseFixtureWindow()

        QTest.mouseClick(window.start_progress_button, Qt.LeftButton)
        QTest.qWait(1500)  # oltre i 5 tick da 150ms dichiarati (750ms), non un'attesa arbitraria

        self.assertEqual(window.progress_bar.value(), 100)

    def test_clicking_start_twice_restarts_from_zero_not_from_where_it_was(self):
        window = ComputerUseFixtureWindow()
        QTest.mouseClick(window.start_progress_button, Qt.LeftButton)
        QTest.qWait(1500)
        self.assertEqual(window.progress_bar.value(), 100)

        QTest.mouseClick(window.start_progress_button, Qt.LeftButton)

        self.assertEqual(window.progress_bar.value(), 0, "un secondo avvio deve ripartire da zero, come gia' scelto per Task 6/10")

    def test_reset_mid_progress_stops_the_timer_for_real(self):
        """Stessa insidia gia' trovata per `_load_timer` (Task 6/10): un reset a meta' che si
        limitasse ad azzerare il valore visibile, senza fermare il `QTimer` ricorrente, verrebbe
        vanificato dal prossimo tick gia' schedulato."""
        window = ComputerUseFixtureWindow()
        QTest.mouseClick(window.start_progress_button, Qt.LeftButton)
        QTest.qWait(200)  # un solo tick, ancora ben lontano da 100

        window.reset_state()
        QTest.qWait(1500)  # abbastanza per completare l'intero avanzamento, se il timer non fosse stato fermato davvero

        self.assertEqual(window.progress_bar.value(), 0, "il timer ricorrente deve essere fermato davvero, non solo il valore azzerato")


class ReorderListTests(unittest.TestCase):
    """Task 16 di F3.1.2 (continua verso i 100): `reorder_list`, riordinabile via drag-and-drop
    (`DragDropMode.InternalMove`) - vedi
    `tests/test_computer_use_integration.py::DragReorderEndToEndTests` per la dimostrazione via UI
    Automation con un trascinamento sintetico reale (mouse down/move/up), inclusa la scoperta che
    l'automation_id del contenitore e' condiviso dai suoi elementi figli."""

    def test_the_initial_order_is_uno_due_tre(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.reorder_list_items(), ["Uno", "Due", "Tre"])

    def test_reset_restores_the_original_order(self):
        window = ComputerUseFixtureWindow()
        window.reorder_list.clear()
        window.reorder_list.addItems(["Due", "Uno", "Tre"])
        self.assertEqual(window.reorder_list_items(), ["Due", "Uno", "Tre"])

        window.reset_state()

        self.assertEqual(window.reorder_list_items(), ["Uno", "Due", "Tre"])


class SpinBoxTests(unittest.TestCase):
    """Task 17 di F3.1.2 (continua verso i 100) - il primo bersaglio della nuova "Tab 3", un
    contenitore dedicato ai task futuri invece di continuare ad allungare la colonna verticale
    principale (buco reale gia' trovato per Task 16, vedi il docstring della fixture). Stesso
    pattern RangeValue di `value_slider` (Task 14), verificato funzionare identicamente su un
    `QSpinBox` - vedi `tests/test_executor.py::RangeValueOnSpinBoxTests` per la dimostrazione via
    UI Automation."""

    def test_the_value_starts_at_zero(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.value_spinbox.value(), 0)

    def test_the_range_is_zero_to_one_hundred(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.value_spinbox.minimum(), 0)
        self.assertEqual(window.value_spinbox.maximum(), 100)

    def test_reset_returns_the_spinbox_to_zero(self):
        window = ComputerUseFixtureWindow()
        window.value_spinbox.setValue(33)
        self.assertEqual(window.value_spinbox.value(), 33)

        window.reset_state()

        self.assertEqual(window.value_spinbox.value(), 0)


class RadioButtonTests(unittest.TestCase):
    """Task 18 di F3.1.2 (continua verso i 100, in "Tab 3"): un gruppo di `QRadioButton`
    mutuamente esclusivi - vedi
    `tests/test_executor.py::RadioButtonMutualExclusivityTests` per la dimostrazione via UI
    Automation che selezionarne uno deseleziona DAVVERO gli altri (il pattern SelectionItem,
    affidabile qui come per un `TabItem`, a differenza della trappola gia' nota per
    `QListWidgetItem`)."""

    def test_red_is_checked_by_default(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.selected_radio_label(), "Rosso")

    def test_checking_a_different_radio_unchecks_the_previous_one(self):
        window = ComputerUseFixtureWindow()
        window.radio_green.setChecked(True)
        self.assertEqual(window.selected_radio_label(), "Verde")
        self.assertFalse(window.radio_red.isChecked())

    def test_reset_returns_to_red_selected(self):
        window = ComputerUseFixtureWindow()
        window.radio_blue.setChecked(True)
        self.assertEqual(window.selected_radio_label(), "Blu")

        window.reset_state()

        self.assertEqual(window.selected_radio_label(), "Rosso")


class TableTests(unittest.TestCase):
    """Task 19 di F3.1.2 (continua verso i 100, in "Tab 4" - MAI in Tab 3: vedi il commento sopra
    `fourth_tab` nel modulo per il buco reale che ha motivato una scheda dedicata) - una griglia
    (`QTableWidget`), il pattern "cella di una tabella" mai esercitato finora in questa fixture."""

    def test_initial_cells_have_the_expected_text(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.table_cell_text(0, 0), "Riga 1")
        self.assertEqual(window.table_cell_text(0, 1), "")
        self.assertEqual(window.table_cell_text(1, 0), "Riga 2")
        self.assertEqual(window.table_cell_text(1, 1), "")

    def test_editing_a_cell_changes_its_text(self):
        window = ComputerUseFixtureWindow()
        window.data_table.item(0, 1).setText("modificato")
        self.assertEqual(window.table_cell_text(0, 1), "modificato")

    def test_reset_clears_edited_cells(self):
        window = ComputerUseFixtureWindow()
        window.data_table.item(0, 1).setText("modificato")
        window.data_table.item(1, 1).setText("anche questo")

        window.reset_state()

        self.assertEqual(window.table_cell_text(0, 1), "")
        self.assertEqual(window.table_cell_text(1, 1), "")
        self.assertEqual(window.table_cell_text(0, 0), "Riga 1", "solo le celle modificabili vanno azzerate, non le etichette di riga")


class TransferListTests(unittest.TestCase):
    """Task 20 di F3.1.2 (continua verso i 100, in "Tab 5") - trascinare un elemento da UNA lista
    a un'ALTRA, diverso da Task 16 (riordino DENTRO la stessa lista): qui l'elemento cambia
    CONTENITORE."""

    def test_initial_state_has_two_items_in_source_and_none_in_target(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.transfer_source_items(), ["Alfa", "Beta"])
        self.assertEqual(window.transfer_target_items(), [])

    def test_reset_restores_the_initial_split(self):
        window = ComputerUseFixtureWindow()
        window.transfer_source_list.takeItem(0)
        window.transfer_target_list.addItem("Alfa")

        window.reset_state()

        self.assertEqual(window.transfer_source_items(), ["Alfa", "Beta"])
        self.assertEqual(window.transfer_target_items(), [])


class MultilineEditTests(unittest.TestCase):
    """Task 27 di F3.1.2 (continua verso i 100, in "Tab 8") - un campo di testo multiriga
    (`QPlainTextEdit`), mai un bersaglio in questa fixture finora - ogni campo gia' esercitato era
    a riga singola."""

    def test_the_field_starts_empty(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.multiline_edit.toPlainText(), "")

    def test_setting_multiline_text_preserves_the_newline(self):
        window = ComputerUseFixtureWindow()
        window.multiline_edit.setPlainText("riga uno\nriga due")
        self.assertEqual(window.multiline_edit.toPlainText(), "riga uno\nriga due")

    def test_reset_clears_the_field(self):
        window = ComputerUseFixtureWindow()
        window.multiline_edit.setPlainText("qualcosa")

        window.reset_state()

        self.assertEqual(window.multiline_edit.toPlainText(), "")


class DateEditTests(unittest.TestCase):
    """Task 22 di F3.1.2 (continua verso i 100, in "Tab 6") - un `QDateEdit` con popup calendario
    (`setCalendarPopup(True)`), MAI un bersaglio in questa fixture finora."""

    def test_the_initial_date_is_the_fixed_start_date(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.current_date_text(), "2026-01-15")

    def test_setting_a_new_date_changes_the_observable_text(self):
        window = ComputerUseFixtureWindow()
        window.date_edit.setDate(QDate(2026, 3, 1))
        self.assertEqual(window.current_date_text(), "2026-03-01")

    def test_reset_returns_to_the_fixed_start_date(self):
        window = ComputerUseFixtureWindow()
        window.date_edit.setDate(QDate(2026, 12, 31))

        window.reset_state()

        self.assertEqual(window.current_date_text(), "2026-01-15")


class FilterListTests(unittest.TestCase):
    """Task 23 di F3.1.2 (continua verso i 100, in "Tab 7") - un campo di ricerca che filtra dal
    vivo una lista (nasconde le righe non corrispondenti, MAI le rimuove/ricrea), un pattern reale
    molto comune mai esercitato finora in questa fixture."""

    def test_all_items_are_visible_with_an_empty_filter(self):
        window = ComputerUseFixtureWindow()
        self.assertEqual(window.visible_filter_items(), ["Mela", "Banana", "Pera", "Mango", "Kiwi"])

    def test_filtering_by_a_substring_hides_non_matching_items(self):
        window = ComputerUseFixtureWindow()
        window.filter_input.setText("an")
        self.assertEqual(window.visible_filter_items(), ["Banana", "Mango"])

    def test_the_filter_is_case_insensitive(self):
        window = ComputerUseFixtureWindow()
        window.filter_input.setText("BANANA")
        self.assertEqual(window.visible_filter_items(), ["Banana"])

    def test_clearing_the_filter_shows_every_item_again(self):
        window = ComputerUseFixtureWindow()
        window.filter_input.setText("an")
        window.filter_input.setText("")
        self.assertEqual(window.visible_filter_items(), ["Mela", "Banana", "Pera", "Mango", "Kiwi"])

    def test_a_filter_matching_nothing_leaves_the_list_empty(self):
        window = ComputerUseFixtureWindow()
        window.filter_input.setText("xyz")
        self.assertEqual(window.visible_filter_items(), [])

    def test_reset_clears_the_filter_and_shows_every_item(self):
        window = ComputerUseFixtureWindow()
        window.filter_input.setText("an")

        window.reset_state()

        self.assertEqual(window.filter_input.text(), "")
        self.assertEqual(window.visible_filter_items(), ["Mela", "Banana", "Pera", "Mango", "Kiwi"])


if __name__ == "__main__":
    unittest.main()
