"""Test unitari per benchmarks/computer_use_fixture.py (F3.1.1, prima fetta della fase Computer
Use Engine 3.0 - "Stato: READY, mai iniziato" prima di questo incremento). Stesso schema di
tests/test_hud_widgets.py per i widget Qt esistenti: una QApplication reale (Qt lo richiede per
costruire i widget), mai una vera finestra mostrata (show()), click simulati con QTest.mouseClick
(un evento Qt vero, non una chiamata diretta allo slot) per provare che il controllo sia davvero
cliccabile, non solo che il suo metodo funzioni se chiamato a mano."""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from benchmarks.computer_use_fixture import ComputerUseFixtureWindow

_app = QApplication.instance() or QApplication([])


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


if __name__ == "__main__":
    unittest.main()
