"""Fixture app Windows per il benchmark di Computer Use (F3.1.1, vedi ROADMAP_EXECUTION.md
sezione F3.1 - "Stato: READY, mai iniziato" prima di questo incremento): una finestra PySide6
minima e isolata, con controlli standard (campo di testo, bottoni, lista) e nomi di automazione
stabili, per dare a un futuro UIAutomationAdapter (F3.2, non ancora costruito) un bersaglio
deterministico e ripetibile - mai un'app o un dato personale reale (criterio di uscita
dichiarato dalla roadmap: "benchmark deterministico eseguibile senza toccare dati o app
personali").

Quinta fetta (una lista con scorrimento, 30 righe in un'area alta poche righe - il pattern Scroll
che F3.4.1 dichiara insieme agli altri, l'ultimo del gruppo "Invoke, Value, Selection, Toggle,
ExpandCollapse, Scroll" ancora senza un bersaglio nella fixture): con questa, `F3.1.1` e' chiuso
per intero - button/input/list/dialog/tree/tabs/scrolling, esattamente l'elenco letterale della
roadmap ("Creare una app fixture Windows con button, input, list, dialog, tree, tabs e
scrolling"). Stesso principio "un incremento alla volta" gia' seguito per tutta la fase F1 in
questa sessione.

**Sesta fetta (19/09/2026, un incremento successivo) - Task 6/10 di F3.1.2, la parte DINAMICA di
F3.1.6 mai affrontata finora**: `load_button`/`dynamic_button` - a differenza di `remove_button`
(gia' un "primo assaggio" di F3.1.6, ma SINCRONO: cambia stato dentro lo stesso gestore di click
che lo scopre), `dynamic_button` si abilita solo dopo un vero `QTimer.singleShot` innescato da
`load_button` - lo stesso genere di attesa che un'app reale impone per un caricamento di rete/
un'operazione lunga, dove un selettore che leggesse lo stato SUBITO dopo il click troverebbe
DAVVERO il controllo ancora nello stato precedente, non un flag gia' cambiato per costruzione.

**Settima fetta (20/09/2026, un incremento successivo) - Task 7/10 di F3.1.2, CHIUDE F3.1.6 per
intero**: `action_button_a`/`action_button_b` - due bottoni con lo STESSO `accessibleName`
("Azione", lo scenario reale di un modulo con "Invia"/"Applica" ripetuto in piu' sezioni) ma
`objectName`/automation_id DIVERSI - il bersaglio DEDICATO per "controlli ambigui" che mancava
ancora in questa fixture (F3.3.2/F3.3.3 avevano gia' dimostrato la stessa cosa altrove con
'Categoria A'/'Categoria B' dell'albero, un caso di RUOLO condiviso non di NOME). Un contatore
INDIPENDENTE per bottone (non condiviso) rende osservabile QUALE dei due e' stato cliccato
davvero, non solo che "un" click sia arrivato da qualche parte - la prova che serve per
dimostrare che l'automation_id sceglie quello GIUSTO, non uno a caso tra i due ambigui.

Deliberatamente NON affrontati qui, passi successivi dichiarati:
- F3.1.2 (3 dei 10 task rimangono ora - sette dimostrati: "aggiungi", "rimuovi con conferma",
  "espandi e seleziona", "cambia tab e spunta l'opzione", "scorri e seleziona l'ultima riga",
  "attendi un controllo dinamico e attivalo", "disambigua due controlli ambigui per nome");
- F3.1.5 (DPI, piu' monitor, finestre sovrapposte, temi diversi);
- l'intera F3.2 (`UIAutomationAdapter`, ancora da costruire).

PySide6 invece di Win32/WinForms nativo: gia' una dipendenza del progetto
(requirements/hud.txt, usata dall'HUD - vedi core/gui/hud/), ed espone i propri widget a UI
Automation su Windows tramite il ponte di accessibilita' di Qt (QAccessible) - non perfettamente
rappresentativo di OGNI tipo di app reale (molte app Windows sono Win32/WinForms/WPF, non Qt),
ma sufficiente per validare i meccanismi di selezione/interazione generici che F3.2+ dovra'
costruire. Limite dichiarato apertamente, non nascosto - F3.7 (adapter applicativi) copre gia'
esplicitamente app non-Qt reali (Esplora file, VS Code, browser, Office...)."""
import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

# Terza fetta (albero): due categorie, due figli ciascuna - nomi stabili anche per i dati, non
# solo per i controlli, cosi' un task puo' riferirsi a "Elemento A1" senza ambiguita'.
_TREE_STRUCTURE = {
    "Categoria A": ["Elemento A1", "Elemento A2"],
    "Categoria B": ["Elemento B1", "Elemento B2"],
}

# Quinta fetta (scorrimento): abbastanza righe da superare qualunque altezza ragionevole
# dell'area visibile (impostata sotto a poche righe con setMaximumHeight), cosi' l'ultima riga
# e' garantita fuori vista finche' qualcuno non scorre davvero, non solo in teoria.
_SCROLL_LIST_ROW_COUNT = 30


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
        self.resize(360, 640)

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

        self.load_button = QPushButton("Carica dati")
        self.load_button.setObjectName("fixture_load_button")
        self.load_button.setAccessibleName("Carica dati")
        self.load_button.clicked.connect(self._start_loading)

        self.dynamic_button = QPushButton("Azione sbloccata")
        self.dynamic_button.setObjectName("fixture_dynamic_button")
        self.dynamic_button.setAccessibleName("Azione sbloccata")
        self.dynamic_button.setEnabled(False)
        self.dynamic_button.clicked.connect(self._activate_dynamic_action)
        self.dynamic_action_activated = False
        # Un QTimer VERO (non la comodita' statica QTimer.singleShot) - serve un riferimento a cui
        # chiedere .stop() da reset_state(). Buco reale trovato scrivendo il test di reset, non
        # ipotizzato: un reset chiamato PRIMA che il timer scada (un task interrotto a meta') non
        # fermava affatto il timer gia' schedulato con QTimer.singleShot - il bottone tornava
        # abilitato DA SOLO ~1s dopo, vanificando il reset in silenzio (verificato riproducendolo:
        # `isEnabled()` era `True` dopo un'attesa, nonostante il reset gia' chiamato).
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(1000)
        self._load_timer.timeout.connect(lambda: self.dynamic_button.setEnabled(True))

        # Task 7/10 di F3.1.2 (F3.1.6 RESTO - "controlli ambigui" - gia' dimostrato altrove in
        # questa sessione con 'Categoria A'/'Categoria B' dell'albero, F3.3.2/F3.3.3, ma MAI con
        # un bersaglio dedicato in QUESTA fixture): due bottoni con lo STESSO accessibleName
        # ("Azione", lo scenario reale di un modulo web con "Invia" ripetuto in piu' sezioni) ma
        # `objectName`/automation_id DIVERSI - un selettore per il solo nome e' deliberatamente
        # ambiguo, richiede automation_id per scegliere quello giusto. Un contatore INDIPENDENTE
        # per bottone (non un contatore condiviso) rende osservabile QUALE dei due e' stato
        # cliccato davvero, non solo che "un" click sia arrivato da qualche parte.
        self.action_button_a = QPushButton("Azione")
        self.action_button_a.setObjectName("fixture_action_a")
        self.action_button_a.setAccessibleName("Azione")
        self.action_button_a.clicked.connect(self._click_action_a)
        self.action_a_clicks = 0

        self.action_button_b = QPushButton("Azione")
        self.action_button_b.setObjectName("fixture_action_b")
        self.action_button_b.setAccessibleName("Azione")
        self.action_button_b.clicked.connect(self._click_action_b)
        self.action_b_clicks = 0

        # I due contatori sopra sono stato IN PROCESSO (utile solo a un test nello stesso
        # processo Qt, stesso limite gia' dichiarato per list_items()) - un test end-to-end VERO
        # guidato dall'esterno (un processo separato, UI Automation) ha bisogno di un modo di
        # OSSERVARLI sullo schermo per dimostrare quale dei due bottoni ambigui e' stato cliccato
        # davvero: questa etichetta e' quel modo, aggiornata dagli stessi due gestori sotto.
        self.action_counts_label = QLabel("A:0 B:0")
        self.action_counts_label.setObjectName("fixture_action_counts")
        # NESSUN setAccessibleName() qui, deliberatamente - a differenza di ogni altro controllo
        # in questa fixture: un accessibleName FISSO congelerebbe il Name esposto a UI Automation
        # al valore dato UNA VOLTA, ignorando ogni `.setText()` successivo (buco reale trovato
        # scrivendo il test end-to-end, non ipotizzato: il test leggeva sempre "Conteggio azioni",
        # mai il conteggio vero). Senza un accessibleName esplicito, il ponte di accessibilita' di
        # Qt deriva il Name di una QLabel dal suo `.text()` corrente - verificato, non assunto -
        # cosi' un aggiornamento reale del testo e' DAVVERO osservabile via UI Automation.

        self.item_list = QListWidget()
        self.item_list.setObjectName("fixture_list")
        self.item_list.setAccessibleName("Elenco elementi")
        self.item_list.itemSelectionChanged.connect(self._update_remove_button_enabled)

        self.tree = QTreeWidget()
        self.tree.setObjectName("fixture_tree")
        self.tree.setAccessibleName("Struttura ad albero")
        self.tree.setHeaderHidden(True)
        self._populate_tree()

        self.tabs = QTabWidget()
        self.tabs.setObjectName("fixture_tabs")
        self.tabs.setAccessibleName("Schede")

        first_tab = QWidget()
        first_tab.setObjectName("fixture_tab_one_content")
        QVBoxLayout(first_tab).addWidget(QLabel("Contenuto del Tab 1"))
        self.tabs.addTab(first_tab, "Tab 1")

        second_tab = QWidget()
        second_tab.setObjectName("fixture_tab_two_content")
        self.option_checkbox = QCheckBox("Opzione")
        self.option_checkbox.setObjectName("fixture_checkbox")
        self.option_checkbox.setAccessibleName("Opzione")
        QVBoxLayout(second_tab).addWidget(self.option_checkbox)
        self.tabs.addTab(second_tab, "Tab 2")

        self.scroll_list = QListWidget()
        self.scroll_list.setObjectName("fixture_scroll_list")
        self.scroll_list.setAccessibleName("Elenco con scorrimento")
        self.scroll_list.setMaximumHeight(90)
        self.scroll_list.addItems([f"Riga {i}" for i in range(1, _SCROLL_LIST_ROW_COUNT + 1)])

        buttons_row = QHBoxLayout()
        buttons_row.addWidget(self.add_button)
        buttons_row.addWidget(self.reset_button)
        buttons_row.addWidget(self.remove_button)

        dynamic_row = QHBoxLayout()
        dynamic_row.addWidget(self.load_button)
        dynamic_row.addWidget(self.dynamic_button)

        ambiguous_row = QHBoxLayout()
        ambiguous_row.addWidget(self.action_button_a)
        ambiguous_row.addWidget(self.action_button_b)
        ambiguous_row.addWidget(self.action_counts_label)

        layout = QVBoxLayout(self)
        layout.addWidget(self.input_field)
        layout.addLayout(buttons_row)
        layout.addWidget(self.item_list)
        layout.addWidget(self.tree)
        layout.addWidget(self.tabs)
        layout.addWidget(self.scroll_list)
        layout.addLayout(dynamic_row)
        layout.addLayout(ambiguous_row)

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

    def current_tab_name(self) -> str:
        """Task 4/10 di F3.1.2 (prima meta': "cambia tab"): il nome della tab ATTIVA - lo stesso
        testo che UI Automation esporrebbe come Name di un TabItem selezionato."""
        return self.tabs.tabText(self.tabs.currentIndex())

    def is_option_checked(self) -> bool:
        """Task 4/10 di F3.1.2 (seconda meta': "spunta l'opzione") - il pattern Toggle (F3.4.1),
        senza ancora un bersaglio nella fixture prima di questa fetta. La casella vive nella
        seconda tab: leggerla senza aver prima cambiato tab e' comunque possibile qui (stato
        Qt sempre presente anche per una tab non visibile), a differenza di un vero click che
        richiederebbe la tab davvero attiva - lo stesso limite gia' dichiarato per
        `list_items()`/`selected_tree_item_text()`."""
        return self.option_checkbox.isChecked()

    def is_scrolled_to_bottom(self) -> bool:
        """Task 5/10 di F3.1.2 (prima meta': "scorri..."): vero solo quando la barra di
        scorrimento e' davvero al suo valore massimo - non un'approssimazione su quante righe
        sono state costruite, il pattern Scroll (F3.4.1) riguarda la POSIZIONE dello scorrimento,
        non l'esistenza del contenuto (che c'e' gia' tutto fin dall'inizio, solo non visibile)."""
        bar = self.scroll_list.verticalScrollBar()
        return bar.value() >= bar.maximum()

    def selected_scroll_item_text(self) -> str | None:
        """Task 5/10 di F3.1.2 (seconda meta': "...e seleziona l'ultima riga"). Stato osservabile
        IN PROCESSO, stesso ripiego onesto di `list_items()`/`selected_tree_item_text()`."""
        current = self.scroll_list.currentItem()
        return current.text() if current is not None else None

    def reset_state(self) -> None:
        """F3.1.3: riporta la fixture allo stato iniziale - lo stesso stato ad ogni avvio di un
        nuovo task, cosi' un task non eredita mai residui lasciati da quello precedente."""
        self.item_list.clear()
        self.input_field.clear()
        self.remove_button.setEnabled(False)
        self.tree.clearSelection()
        self._populate_tree()
        self.tabs.setCurrentIndex(0)
        self.option_checkbox.setChecked(False)
        # Buco reale trovato scrivendo il test di reset, non ipotizzato: clearSelection() da
        # solo NON basta - in Qt "selezione" e "elemento corrente" (currentItem/currentRow) sono
        # due concetti distinti, e selected_scroll_item_text() legge il secondo. Senza
        # setCurrentRow(-1), l'ultima riga scelta restava "corrente" (quindi ancora restituita
        # da selected_scroll_item_text()) anche dopo un reset che sembrava completo.
        self.scroll_list.clearSelection()
        self.scroll_list.setCurrentRow(-1)
        self.scroll_list.scrollToTop()
        # .stop() PRIMA di disabilitare di nuovo il bottone - un reset chiamato PRIMA che il
        # timer sia scaduto (un task interrotto a meta') deve fermare DAVVERO l'operazione
        # pendente, non solo azzerare lo stato visibile lasciando che il timer originale la
        # riabiliti da solo qualche istante dopo (vedi il docstring di _start_loading per la
        # prova empirica di questo buco, trovato scrivendo il test di reset).
        self._load_timer.stop()
        self.dynamic_button.setEnabled(False)
        self.dynamic_action_activated = False
        self.action_a_clicks = 0
        self.action_b_clicks = 0
        self._update_action_counts_label()

    def _click_action_a(self) -> None:
        self.action_a_clicks += 1
        self._update_action_counts_label()

    def _click_action_b(self) -> None:
        self.action_b_clicks += 1
        self._update_action_counts_label()

    def _update_action_counts_label(self) -> None:
        self.action_counts_label.setText(f"A:{self.action_a_clicks} B:{self.action_b_clicks}")

    def _start_loading(self) -> None:
        """Task 6/10 di F3.1.2 (F3.1.6, "controlli dinamici" - la parte MAI affrontata finora:
        `remove_button`, F3.1.6 "un primo assaggio", cambia stato in modo SINCRONO dentro lo
        stesso gestore di click che lo scopre; questo bottone appare/si abilita in un momento
        DIVERSO e successivo, dopo un `QTimer` - lo stesso genere di attesa che un'app reale
        impone per un caricamento di rete/un'operazione lunga, non riproducibile da un controllo
        gia' presente e sincrono). Un vero `QTimer` (non un semplice flag booleano settato
        subito) cosi' un selettore che leggesse lo stato SUBITO dopo il click troverebbe DAVVERO
        il bottone ancora disabilitato - la stessa distinzione "sincrono vs poi, davvero" gia' al
        centro di F3.4.7. `.start()` (non `QTimer.singleShot`, la comodita' statica usata in una
        prima versione) riavvia il conto alla rovescia da capo se cliccato una seconda volta -
        coerente con "carica di nuovo" invece di lasciare un timer piu' vecchio, gia' in corso,
        decidere quando il bottone si abilita."""
        self._load_timer.stop()
        self.dynamic_button.setEnabled(False)
        self._load_timer.start()

    def _activate_dynamic_action(self) -> None:
        """Il bottone dinamico, una volta abilitato DAVVERO dal timer sopra, si comporta come
        qualunque altro bottone - il punto di F3.1.6 e' la sua APPARIZIONE ritardata, non
        un'azione speciale una volta raggiungibile."""
        self.dynamic_action_activated = True

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
