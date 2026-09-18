"""Test unitari per benchmarks/computer_use_fixture.py (F3.1.1, prima fetta della fase Computer
Use Engine 3.0 - "Stato: READY, mai iniziato" prima di questo incremento). Stesso schema di
tests/test_hud_widgets.py per i widget Qt esistenti: una QApplication reale (Qt lo richiede per
costruire i widget), mai una vera finestra mostrata (show()), click simulati con QTest.mouseClick
(un evento Qt vero, non una chiamata diretta allo slot) per provare che il controllo sia davvero
cliccabile, non solo che il suo metodo funzioni se chiamato a mano."""
import unittest

from PySide6.QtCore import Qt, QTimer
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

    def test_every_control_has_a_non_empty_accessible_name(self):
        window = ComputerUseFixtureWindow()
        for widget in (window.input_field, window.add_button, window.reset_button, window.item_list):
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


if __name__ == "__main__":
    unittest.main()
