"""Fixture app Windows per il benchmark di Computer Use (F3.1.1, vedi ROADMAP_EXECUTION.md
sezione F3.1 - "Stato: READY, mai iniziato" prima di questo incremento): una finestra PySide6
minima e isolata, con controlli standard (campo di testo, bottoni, lista) e nomi di automazione
stabili, per dare a un futuro UIAutomationAdapter (F3.2, non ancora costruito) un bersaglio
deterministico e ripetibile - mai un'app o un dato personale reale (criterio di uscita
dichiarato dalla roadmap: "benchmark deterministico eseguibile senza toccare dati o app
personali").

Terza fetta (un albero a due livelli, collassato per default - il pattern ExpandCollapse che
F3.4.1 dichiara esplicitamente tra quelli da implementare, insieme a Selection): espandere una
categoria e selezionare un figlio e' un compito che NESSUN altro controllo della fixture prova
ancora (Selection da sola non basta: senza espandere prima, il nodo figlio non e' nemmeno nella
"struttura visibile" che un selettore percettivo puo' trovare). Stesso principio "un incremento
alla volta" gia' seguito per tutta la fase F1 in questa sessione - deliberatamente NON
affrontati qui, passi successivi dichiarati:
- il resto di F3.1.1 (tabs, scrolling - non ancora presenti);
- F3.1.2 (7 dei 10 task rimangono - tre dimostrati qui: "aggiungi", "rimuovi con conferma",
  "espandi e seleziona");
- F3.1.5 (DPI, piu' monitor, finestre sovrapposte, temi diversi);
- F3.1.6 (controlli ambigui e dinamici - i controlli DISABILITATI hanno gia' un primo assaggio
  col bottone "Rimuovi selezionato", disabilitato senza una selezione, ma non e' l'intero punto
  dichiarato da quella fetta).

PySide6 invece di Win32/WinForms nativo: gia' una dipendenza del progetto
(requirements/hud.txt, usata dall'HUD - vedi core/gui/hud/), ed espone i propri widget a UI
Automation su Windows tramite il ponte di accessibilita' di Qt (QAccessible) - non perfettamente
rappresentativo di OGNI tipo di app reale (molte app Windows sono Win32/WinForms/WPF, non Qt),
ma sufficiente per validare i meccanismi di selezione/interazione generici che F3.2+ dovra'
costruire. Limite dichiarato apertamente, non nascosto - F3.7 (adapter applicativi) copre gia'
esplicitamente app non-Qt reali (Esplora file, VS Code, browser, Office...)."""
import argparse
import sys

from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLineEdit, QListWidget, QMessageBox, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

# Terza fetta (albero): due categorie, due figli ciascuna - nomi stabili anche per i dati, non
# solo per i controlli, cosi' un task puo' riferirsi a "Elemento A1" senza ambiguita'.
_TREE_STRUCTURE = {
    "Categoria A": ["Elemento A1", "Elemento A2"],
    "Categoria B": ["Elemento B1", "Elemento B2"],
}


class ComputerUseFixtureWindow(QWidget):
    """Finestra fixture: un campo di testo + un bottone 'Aggiungi' che sposta il testo digitato
    in una lista, + un bottone 'Reset' che riporta tutto allo stato iniziale (F3.1.3,
    "resettare lo stato della fixture prima di ogni task"). Ogni controllo ha un `objectName`
    (per un selettore procedurale, F3.3.1/F3.3.4) e un `accessibleName` esplicito (il nome che
    UI Automation espone davvero, F3.2.3) - mai lasciati al default Qt, che non è stabile ne'
    leggibile: un selettore futuro deve poter trovare questi controlli per NOME, non per
    posizione sullo schermo (la stessa fragilita' che F3.3 esiste per eliminare)."""

    WINDOW_TITLE = "Jake Computer Use Fixture"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setObjectName("jake_fixture_window")
        self.resize(360, 480)

        self.input_field = QLineEdit()
        self.input_field.setObjectName("fixture_input")
        self.input_field.setAccessibleName("Campo di testo")
        self.input_field.setPlaceholderText("Scrivi qualcosa...")

        self.add_button = QPushButton("Aggiungi")
        self.add_button.setObjectName("fixture_add_button")
        self.add_button.setAccessibleName("Aggiungi")
        self.add_button.clicked.connect(self._add_current_text)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("fixture_reset_button")
        self.reset_button.setAccessibleName("Reset")
        self.reset_button.clicked.connect(self.reset_state)

        self.remove_button = QPushButton("Rimuovi selezionato")
        self.remove_button.setObjectName("fixture_remove_button")
        self.remove_button.setAccessibleName("Rimuovi selezionato")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self._remove_selected_with_confirmation)

        self.item_list = QListWidget()
        self.item_list.setObjectName("fixture_list")
        self.item_list.setAccessibleName("Elenco elementi")
        self.item_list.itemSelectionChanged.connect(self._update_remove_button_enabled)

        self.tree = QTreeWidget()
        self.tree.setObjectName("fixture_tree")
        self.tree.setAccessibleName("Struttura ad albero")
        self.tree.setHeaderHidden(True)
        self._populate_tree()

        buttons_row = QHBoxLayout()
        buttons_row.addWidget(self.add_button)
        buttons_row.addWidget(self.reset_button)
        buttons_row.addWidget(self.remove_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.input_field)
        layout.addLayout(buttons_row)
        layout.addWidget(self.item_list)
        layout.addWidget(self.tree)

    def _add_current_text(self) -> None:
        """Task 1/10 di F3.1.2 (gli altri 9 restano un passo successivo dichiarato): digitare un
        testo nel campo e cliccare 'Aggiungi' deve farlo comparire nella lista. Un campo vuoto
        non aggiunge nulla - stesso principio "nessun effetto per un input vuoto" gia' applicato
        altrove nel progetto (es. skills/notes.py), non specifico di questa fixture."""
        text = self.input_field.text().strip()
        if not text:
            return
        self.item_list.addItem(text)
        self.input_field.clear()

    def _populate_tree(self) -> None:
        """Due categorie, due figli ciascuna, TUTTE collassate per default (`setExpanded(False)`
        esplicito - il default Qt e' gia' collassato, ma dichiararlo qui rende lo stato iniziale
        un fatto verificato, non un'assunzione sul comportamento di default della libreria). Il
        NOME di ogni nodo (colonna 0) e' gia' cio' che UI Automation esporrebbe come Name di un
        TreeItem - a differenza di un QWidget, un QTreeWidgetItem non e' un QObject e non ha un
        proprio objectName da impostare, il testo visibile e' gia' l'unico identificatore stabile
        (per questo i nomi in `_TREE_STRUCTURE` sono tutti diversi tra loro)."""
        self.tree.clear()
        for category_name, children in _TREE_STRUCTURE.items():
            category_item = QTreeWidgetItem([category_name])
            self.tree.addTopLevelItem(category_item)
            for child_name in children:
                category_item.addChild(QTreeWidgetItem([child_name]))
            category_item.setExpanded(False)

    def selected_tree_item_text(self) -> str | None:
        """Stato osservabile IN PROCESSO (stesso ripiego onesto di `list_items()` - vedi li' per
        il perche' non e' ancora un'osservazione vera tramite UI Automation)."""
        current = self.tree.currentItem()
        return current.text(0) if current is not None else None

    def is_category_expanded(self, category_name: str) -> bool:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.text(0) == category_name:
                return item.isExpanded()
        raise ValueError(f"categoria sconosciuta: {category_name}")

    def reset_state(self) -> None:
        """F3.1.3: riporta la fixture allo stato iniziale - lo stesso stato ad ogni avvio di un
        nuovo task, cosi' un task non eredita mai residui lasciati da quello precedente."""
        self.item_list.clear()
        self.input_field.clear()
        self.remove_button.setEnabled(False)
        self.tree.clearSelection()
        self._populate_tree()

    def _update_remove_button_enabled(self) -> None:
        """Un piccolo assaggio anticipato di F3.1.6 ("controlli disabilitati"): senza una
        selezione, il bottone e' disabilitato per davvero (Qt non emette clicked() su un
        QPushButton disabilitato) - un selettore futuro deve poter leggere `enabled` (F3.2.3)
        prima di tentare un click, non scoprirlo solo perche' il click non ha avuto effetto."""
        self.remove_button.setEnabled(self.item_list.currentItem() is not None)

    def _build_confirmation_dialog(self, item_text: str) -> QMessageBox:
        """Costruita separatamente da `_remove_selected_with_confirmation` cosi' un test puo'
        ispezionare i nomi di automazione dei suoi bottoni senza dover mostrare un vero dialogo
        MODALE (`QMessageBox.exec()` blocca con una propria coda di eventi finche' l'utente non
        risponde - lo stesso ostacolo dichiarato in F3.4.7). Bottoni costruiti a mano (non
        `QMessageBox.question()`, la scorciatoia statica) perche' serve poter dare loro un
        `objectName`/`accessibleName` espliciti PRIMA di mostrarli."""
        dialog = QMessageBox(self)
        dialog.setObjectName("fixture_confirm_dialog")
        dialog.setWindowTitle("Conferma")
        dialog.setText(f'Vuoi davvero rimuovere "{item_text}"?')
        yes_button = dialog.addButton("Sì", QMessageBox.YesRole)
        yes_button.setObjectName("fixture_confirm_yes")
        yes_button.setAccessibleName("Sì")
        no_button = dialog.addButton("No", QMessageBox.NoRole)
        no_button.setObjectName("fixture_confirm_no")
        no_button.setAccessibleName("No")
        dialog.setDefaultButton(no_button)
        return dialog

    def _remove_selected_with_confirmation(self) -> None:
        """Task 2/10 di F3.1.2: rimuovere l'elemento selezionato richiede di passare da un
        dialogo modale di conferma - l'elemento sparisce SOLO se il bottone "Sì" e' quello
        davvero cliccato (`clickedButton()`, non una chiusura qualsiasi del dialogo, che in Qt
        equivale a nessun bottone cliccato)."""
        current = self.item_list.currentItem()
        if current is None:
            return
        dialog = self._build_confirmation_dialog(current.text())
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is not None and clicked.objectName() == "fixture_confirm_yes":
            self.item_list.takeItem(self.item_list.row(current))

    def list_items(self) -> list[str]:
        """Stato osservabile IN PROCESSO (per un test/benchmark che condivide lo stesso processo
        Qt, come questo modulo). Un'osservazione vera tramite UI Automation e' compito
        dell'adapter di F3.2 (non ancora costruito): questo e' il ripiego onesto per verificare
        la fixture stessa finche' quell'adapter non esiste, non una scorciatoia permanente."""
        return [self.item_list.item(i).text() for i in range(self.item_list.count())]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auto-close-after", type=float, default=None,
        help="Chiude la finestra da sola dopo N secondi (per lanci automatizzati/CI, mai per l'uso manuale).",
    )
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    window = ComputerUseFixtureWindow()
    window.show()

    if args.auto_close_after is not None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(int(args.auto_close_after * 1000), app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
