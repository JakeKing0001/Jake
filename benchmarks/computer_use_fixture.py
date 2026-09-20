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

**Ottava fetta (20/09/2026, un incremento successivo) - Task 8/10 di F3.1.2, F3.3.7 (resto -
"tema")**: `_apply_dark_theme()`/`--dark-theme` - un cambio di tema VERO (palette Fusion scura),
non un flag cosmetico. Deliberatamente NON tocca `accessibleName`/`objectName`/control type - le
uniche proprieta' che UI Automation espone (F3.2.3) - cosi' un selettore per nome/automation_id
sopravvive per costruzione, verificato con uno screenshot reale in
`tests/test_selector.py::ThemeChangeTests`, non solo assunto dal fatto che il flag non sollevi.

**Nona fetta (20/09/2026, un incremento successivo) - Task 9/10 di F3.1.2, F3.3.7 (resto -
"traduzione", CHIUDE F3.3.7 per intero)**: `_ADD_BUTTON_LABELS`/`--language` - SOLO il bottone
"Aggiungi"/"Add" e' tradotto (vedi il commento accanto a `_ADD_BUTTON_LABELS` per il motivo di
non tradurre l'intera fixture). L'`automation_id` (`fixture_add_button`) resta identico in ogni
lingua per costruzione - un selettore per automation_id sopravvive alla traduzione, uno per nome
no (deve essere aggiornato per la lingua corrente), verificato in
`tests/test_selector.py::TranslationChangeTests`.

Deliberatamente NON affrontati qui, passi successivi dichiarati:
- F3.1.2 (1 dei 10 task rimane ora - nove dimostrati: "aggiungi", "rimuovi con conferma",
  "espandi e seleziona", "cambia tab e spunta l'opzione", "scorri e seleziona l'ultima riga",
  "attendi un controllo dinamico e attivalo", "disambigua due controlli ambigui per nome", "trova
  e agisci sotto un tema diverso", "trova e agisci sotto una lingua diversa");
- F3.1.5 (DPI, piu' monitor, finestre sovrapposte - "tema"/"lingua" sono ora coperti da F3.3.7
  sopra);
- l'intera F3.2 (`UIAutomationAdapter`, ancora da costruire).

**Decima fetta (20/09/2026, un incremento successivo) - Task 10/10 di F3.1.2, CHIUDE i "10 task
iniziali" dichiarati dalla roadmap per intero**: nessun codice nuovo necessario in QUESTO file -
l'ordine di tabulazione gia' esistente (`input_field` -> `add_button`, il naturale ordine di
inserimento nel layout Qt, verificato empiricamente con un probe dedicato PRIMA di scrivere il
test, non assunto) basta a dimostrare un flusso guidato SOLO dalla tastiera (`SetFocus()` + testo
digitato con tasti veri + Tab + Spazio), mai il mouse - vedi
`tests/test_computer_use_integration.py::KeyboardOnlyNavigationEndToEndTests`.

**Undicesima fetta (20/09/2026, un incremento successivo) - Task 11 di F3.1.2, continua oltre i
"10 task iniziali" verso i 100 dichiarati dal criterio di uscita di F3**: `item_list` passata da
`SingleSelection` a `ExtendedSelection` (Ctrl+Click aggiunge alla selezione) - verificato
retrocompatibile con Task 2/10 PRIMA del cambio (`_update_remove_button_enabled`/
`_remove_selected_with_confirmation` usano gia' solo `currentItem()`, invariato da
`ExtendedSelection` per un click senza modificatori) - vedi
`tests/test_computer_use_integration.py::MultiSelectEndToEndTests`.

**Dodicesima fetta (20/09/2026, un incremento successivo) - Task 12 di F3.1.2**: `option_combo`
(`QComboBox`) - un terzo genere di controllo a selezione, mai presente in questa fixture finora
(diverso sia dalla lista sia dall'albero). Scoperta empirica in due meta': APRIRE il popup
funziona gia' semanticamente via UI Automation (`ExpandCollapsePattern.Expand()`), SELEZIONARE
un'opzione dal popup NO (un `Invoke()` UIA su un `ListItem` del popup non ha alcun effetto -
stessa classe di buco gia' nota per `QListWidgetItem`, F3.4/F3.5) - richiede un click reale a
coordinate pixel, verificato con un probe dedicato PRIMA di scrivere il test - vedi
`tests/test_computer_use_integration.py::ComboBoxSelectionEndToEndTests`.

**Tredicesima fetta (20/09/2026, un incremento successivo) - Task 13 di F3.1.2**: un menu
contestuale reale (`item_list.customContextMenuRequested`, azione "Duplica") - un `QMenu` NON
compare nell'enumerazione dei figli del desktop secondo UI Automation, ma esiste davvero come
finestra Win32 (buco reale RISOLTO, non solo documentato - vedi
`core/computer_use/ui_automation_adapter.py::snapshot_win32_top_level_window_handles`/
`element_from_handle`, che risolvono la STESSA classe di buco anche per il dialogo nativo "Apri"
di Windows, F3.6.3). Selezionare la voce del menu richiede, come per Task 12, un click reale a
coordinate pixel - vedi `tests/test_computer_use_integration.py::ContextMenuEndToEndTests`.

**Quattordicesima fetta (20/09/2026, un incremento successivo) - Task 14 di F3.1.2**: un cursore
(`value_slider`, `QSlider`) - un OTTAVO pattern UI Automation (`RangeValue`), mai dichiarato
dall'elenco originale di F3.4.1 ma aggiunto quando questo controllo e' comparso nella fixture. A
differenza di lista/combobox/menu (tutti richiedono un click pixel reale per selezionare),
`RangeValue.SetValue()` funziona GIA' correttamente via UI Automation pura - verificato con un
probe dedicato PRIMA di scrivere `ActionExecutor.set_range_value()` - vedi
`tests/test_executor.py::RangeValueTests`.

**Quindicesima fetta (20/09/2026, un incremento successivo) - Task 15 di F3.1.2**: una barra di
avanzamento REALE (`progress_bar`/`start_progress_button`, un `QTimer` ricorrente che la riempie
in cinque passi da 20, non un salto istantaneo a 100) - lo scenario motivante e' "aspetta che
un'operazione lunga raggiunga il 100%", diverso da Task 6/10 (un controllo booleano abilitato
dopo un ritardo) perche' qui il VALORE intermedio stesso e' il segnale da osservare, non solo
uno stato finale. Stesso pattern `RangeValue` gia' verificato funzionante per Task 14 - vedi
`tests/test_computer_use_integration.py::ProgressBarEndToEndTests` per la prova via UI Automation
(polling del valore nel tempo, verifica che passi DAVVERO per valori intermedi reali).

**Sedicesima fetta (20/09/2026, un incremento successivo) - Task 16 di F3.1.2**: una lista
riordinabile via trascinamento reale del mouse (`reorder_list`, `DragDropMode.InternalMove`) - una
modalita' di interazione MAI esercitata finora, diversa da click/tastiera/RangeValue. Verificato
con un probe dedicato PRIMA di scrivere il test: un trascinamento SINTETICO (`pyautogui.moveTo`+
`mouseDown`+piu' `moveTo` intermedi+`mouseUp`) viene onorato dal motore di drag-and-drop di Qt - il
riordino avviene per davvero, non solo che la chiamata non sollevi.

**Buco reale trovato nello STESSO probe, non ipotizzato**: l'automation_id del contenitore
(`fixture_reorder_list`) e' CONDIVISO dai suoi `ListItem` figli - verificato che lo stesso buco
esiste GIA' per `fixture_list` (non e' specifico di questa lista nuova, Qt deriva l'automation_id
di un `QListWidgetItem` dallo stesso percorso qualificato del contenitore) - un selettore per il
SOLO automation_id del contenitore e' quindi AMBIGUO appena la lista ha almeno un elemento.

**Secondo buco reale, DIVERSO e piu' insidioso, trovato scrivendo il TEST end-to-end (non il
probe) - una vera REGRESSIONE causata da questo stesso incremento**: aggiungere `reorder_list` (e
prima ancora `progress_bar`/`value_slider`/`option_combo`, Task 12-15) senza un'altezza MINIMA
esplicita per ciascuno ha fatto SI' che `item_list` (mai toccata direttamente da nessuno di questi
incrementi) competesse per sempre meno spazio verticale disponibile nel layout, fino a mostrare
solo ~2 righe senza scorrimento invece delle 3 che `MultiSelectEndToEndTests` (Task 11, scritto
quando c'era ancora spazio a sufficienza) assume gia' visibili - un click sul terzo elemento
aggiunto finiva SOTTO l'area visibile della lista, colpendo per davvero il widget successivo nel
layout (`tree`), non l'elemento cercato. La STESSA identica classe di buco si e' poi ripresentata
per `reorder_list` stessa (il suo terzo elemento, "Tre", parzialmente tagliato fuori). Corretto
dando a ENTRAMBE le liste un `setMinimumHeight(140)` esplicito - verificato leggendo i bounds
REALI via UI Automation dopo il fix, non assunto per analogia. Lezione generale per il resto di
questa fixture: ogni lista con un contenuto potenzialmente multi-riga merita un'altezza minima
dichiarata fin dall'inizio, non lasciata al caso di quanto spazio rimane libero nel layout.

PySide6 invece di Win32/WinForms nativo: gia' una dipendenza del progetto
(requirements/hud.txt, usata dall'HUD - vedi core/gui/hud/), ed espone i propri widget a UI
Automation su Windows tramite il ponte di accessibilita' di Qt (QAccessible) - non perfettamente
rappresentativo di OGNI tipo di app reale (molte app Windows sono Win32/WinForms/WPF, non Qt),
ma sufficiente per validare i meccanismi di selezione/interazione generici che F3.2+ dovra'
costruire. Limite dichiarato apertamente, non nascosto - F3.7 (adapter applicativi) copre gia'
esplicitamente app non-Qt reali (Esplora file, VS Code, browser, Office...)."""
import argparse
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMenu, QMessageBox, QProgressBar, QPushButton, QSlider, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
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

# Task 16 (F3.1.2 continua verso i 100): ordine iniziale della lista riordinabile - tre nomi
# distinti (mai "Riga 1"/"Riga 2" come la scroll_list, per non confondere le due liste in un
# eventuale controllo per nome che cerchi nell'intera finestra).
_REORDER_LIST_ITEMS = ["Uno", "Due", "Tre"]

# Nona fetta (F3.3.7 resto - "traduzione"): SOLO il bottone "Aggiungi" e' tradotto, non l'intera
# fixture - il punto da dimostrare (un selettore per automation_id sopravvive alla lingua, uno per
# nome no) non richiede una i18n completa, e tradurre OGNI stringa (albero/tab/checkbox) userebbe
# ogni test esistente che gia' asserisce quei nomi in italiano, un rischio di regressione senza
# alcun beneficio aggiuntivo - "un incremento alla volta", lo stesso principio gia' seguito per
# ogni fetta precedente di questa fixture. L'`objectName`/automation_id ("fixture_add_button")
# resta IDENTICO in ogni lingua per costruzione (mai stato nel dizionario) - e' esattamente il
# punto da dimostrare.
_ADD_BUTTON_LABELS = {"it": "Aggiungi", "en": "Add"}


class ComputerUseFixtureWindow(QWidget):
    """Finestra fixture: un campo di testo + un bottone 'Aggiungi' che sposta il testo digitato
    in una lista, + un bottone 'Reset' che riporta tutto allo stato iniziale (F3.1.3,
    "resettare lo stato della fixture prima di ogni task"). Ogni controllo ha un `objectName`
    (per un selettore procedurale, F3.3.1/F3.3.4) e un `accessibleName` esplicito (il nome che
    UI Automation espone davvero, F3.2.3) - mai lasciati al default Qt, che non è stabile ne'
    leggibile: un selettore futuro deve poter trovare questi controlli per NOME, non per
    posizione sullo schermo (la stessa fragilita' che F3.3 esiste per eliminare)."""

    WINDOW_TITLE = "Jake Computer Use Fixture"

    def __init__(self, language: str = "it") -> None:
        super().__init__()
        if language not in _ADD_BUTTON_LABELS:
            raise ValueError(f"lingua sconosciuta: {language!r} (attese: {sorted(_ADD_BUTTON_LABELS)})")
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setObjectName("jake_fixture_window")
        self.resize(360, 640)

        self.input_field = QLineEdit()
        self.input_field.setObjectName("fixture_input")
        self.input_field.setAccessibleName("Campo di testo")
        self.input_field.setPlaceholderText("Scrivi qualcosa...")

        add_button_label = _ADD_BUTTON_LABELS[language]
        self.add_button = QPushButton(add_button_label)
        self.add_button.setObjectName("fixture_add_button")
        self.add_button.setAccessibleName(add_button_label)
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
        # Buco reale trovato scrivendo Task 16, non ipotizzato: senza un'altezza MINIMA esplicita,
        # `item_list` competeva per lo spazio verticale con ogni widget aggiunto DOPO di lei nel
        # layout (Task 11-16) - abbastanza spazio e' rimasto per mostrare solo ~2 righe senza
        # scorrimento, non le 3 che `tests/test_computer_use_integration.py::
        # MultiSelectEndToEndTests` (Task 11, scritto quando c'era ancora spazio a sufficienza)
        # assume gia' visibili: un click su un terzo elemento aggiunto finiva SOTTO l'area
        # visibile della lista, colpendo per davvero il widget successivo nel layout (`tree`), non
        # l'elemento cercato - verificato leggendo i bounds reali via UI Automation, non assunto.
        self.item_list.setMinimumHeight(140)
        # Task 11/10 (F3.1.2 continua oltre i "10 iniziali" verso i 100 dichiarati dal criterio di
        # uscita di F3): ExtendedSelection (Ctrl+Click aggiunge alla selezione, Shift+Click
        # seleziona un intervallo) invece del default SingleSelection - retrocompatibile con Task
        # 2/10 ("rimuovi con conferma", verificato: quella logica usa gia' solo `currentItem()`,
        # che ExtendedSelection continua a tracciare esattamente come prima per un click singolo
        # senza modificatori).
        self.item_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Task 13 (F3.1.2 continua verso i 100): un menu contestuale reale (tasto destro), mai un
        # bersaglio in questa fixture finora - da verificare empiricamente se si comporta come il
        # popup gia' noto di QComboBox (un discendente della finestra, Task 12) o come il dialogo
        # nativo di Windows gia' trovato NON raggiungibile via UI Automation (F3.6.3, upload).
        self.item_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.item_list.customContextMenuRequested.connect(self._show_item_context_menu)

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

        # Task 16 (F3.1.2 continua verso i 100): una lista riordinabile via trascinamento reale
        # del mouse (`DragDropMode.InternalMove`) - MAI un bersaglio in questa fixture finora,
        # una modalita' di interazione DIVERSA da click/tastiera/RangeValue gia' esercitati - da
        # verificare empiricamente se un trascinamento SINTETICO (mouseDown+moveTo+mouseUp via
        # pyautogui, non un vero gesto utente) viene onorato dal motore di drag-and-drop di Qt.
        self.reorder_list = QListWidget()
        self.reorder_list.setObjectName("fixture_reorder_list")
        self.reorder_list.setAccessibleName("Elenco riordinabile")
        self.reorder_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.reorder_list.addItems(_REORDER_LIST_ITEMS)
        # Stessa lezione appena imparata per `item_list` (vedi il suo commento) applicata qui fin
        # dall'inizio: senza un'altezza minima esplicita, i suoi tre elementi non entrerebbero
        # tutti nell'area visibile in fondo a una finestra ormai alta, con l'ultimo parzialmente
        # tagliato fuori - verificato leggendo i bounds reali, non assunto per analogia.
        self.reorder_list.setMinimumHeight(140)

        # Dodicesima fetta (F3.1.2 Task 12, continua verso i 100): un menu a tendina, il pattern
        # ExpandCollapse+Selection di un QComboBox - MAI un bersaglio in questa fixture finora,
        # diverso sia dalla lista (F3.4.1) sia dall'albero (gia' noto NON esporre i figli via UIA,
        # F3.4/F3.5) - da verificare empiricamente se lo stesso limite si applica qui.
        self.option_combo = QComboBox()
        self.option_combo.setObjectName("fixture_combo")
        self.option_combo.setAccessibleName("Opzione a tendina")
        self.option_combo.addItems(["Opzione 1", "Opzione 2", "Opzione 3"])

        # Task 14 (F3.1.2 continua verso i 100): un cursore (RangeValue, MAI un pattern
        # dichiarato/esercitato finora in questo progetto - Invoke/Value/Selection/Toggle/
        # ExpandCollapse/Scroll/Window erano gia' tutti coperti, F3.4.1) - da verificare
        # empiricamente se supporta il pattern nativamente o richiede lo stesso ripiego a
        # tastiera/pixel gia' visto per i controlli precedenti.
        self.value_slider = QSlider(Qt.Orientation.Horizontal)
        self.value_slider.setObjectName("fixture_slider")
        self.value_slider.setAccessibleName("Cursore valore")
        self.value_slider.setMinimum(0)
        self.value_slider.setMaximum(100)
        self.value_slider.setValue(0)

        # Task 15 (F3.1.2 continua verso i 100): una barra di avanzamento REALE che si riempie nel
        # tempo (un `QTimer` ricorrente, non un salto istantaneo a 100) - lo scenario motivante e'
        # "aspetta che un'operazione lunga raggiunga il 100%", diverso da "attendi che un controllo
        # diventi abilitato" (Task 6/10, gia' coperto) perche' qui il VALORE intermedio stesso e'
        # il segnale da osservare, non solo uno stato booleano finale.
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("fixture_progress")
        self.progress_bar.setAccessibleName("Barra di avanzamento")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.start_progress_button = QPushButton("Avvia progresso")
        self.start_progress_button.setObjectName("fixture_start_progress_button")
        self.start_progress_button.setAccessibleName("Avvia progresso")
        self.start_progress_button.clicked.connect(self._start_progress)

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(150)
        self._progress_timer.timeout.connect(self._advance_progress)

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
        layout.addWidget(self.option_combo)
        layout.addWidget(self.value_slider)
        layout.addWidget(self.start_progress_button)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.reorder_list)

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
        self.option_combo.setCurrentIndex(0)
        self.value_slider.setValue(0)
        # .stop() PRIMA di azzerare il valore - stesso principio gia' applicato a `_load_timer`
        # sopra: un reset chiamato A META' di un avanzamento deve fermare DAVVERO il timer
        # ricorrente, non solo azzerare lo stato visibile lasciando che il prossimo tick lo
        # rialzi subito dopo.
        self._progress_timer.stop()
        self.progress_bar.setValue(0)
        self.reorder_list.clear()
        self.reorder_list.addItems(_REORDER_LIST_ITEMS)

    def current_combo_option(self) -> str:
        """Stato osservabile IN PROCESSO (stesso ripiego onesto di `list_items()`)."""
        return self.option_combo.currentText()

    def reorder_list_items(self) -> list[str]:
        """Stato osservabile IN PROCESSO (stesso ripiego onesto di `list_items()`) - l'ORDINE
        conta qui, a differenza delle altre liste della fixture."""
        return [self.reorder_list.item(i).text() for i in range(self.reorder_list.count())]

    def _start_progress(self) -> None:
        """Task 15 di F3.1.2: riavvia SEMPRE da zero (non riprende da dove si era fermata) - lo
        stesso comportamento gia' scelto per `_start_loading` (Task 6/10, il secondo click
        riavvia il conto alla rovescia da capo, non lo estende)."""
        self.progress_bar.setValue(0)
        self._progress_timer.start()

    def _advance_progress(self) -> None:
        new_value = min(100, self.progress_bar.value() + 20)
        self.progress_bar.setValue(new_value)
        if new_value >= 100:
            self._progress_timer.stop()

    def _show_item_context_menu(self, pos) -> None:
        """Task 13 di F3.1.2: tasto destro su un elemento della lista mostra un menu con
        "Duplica" - duplicarlo aggiunge una SECONDA copia identica, l'effetto osservabile che
        prova che l'azione del menu e' arrivata DAVVERO, non solo che il menu si sia aperto."""
        item = self.item_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self.item_list)
        duplicate_action = menu.addAction("Duplica")
        chosen = menu.exec(self.item_list.mapToGlobal(pos))
        if chosen is duplicate_action:
            self.item_list.addItem(item.text())

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


def _apply_dark_theme(app: QApplication) -> None:
    """Ottava fetta (20/09/2026) - Task 8/10 di F3.1.2, F3.3.7 (resto - "tema"): un cambio di tema
    VERO (palette Fusion scura, non un semplice `setStyleSheet` cosmetico che potrebbe non
    applicarsi uniformemente a ogni widget) - deliberatamente NON tocca MAI `accessibleName`/
    `objectName`/control type, le uniche proprieta' che UI Automation espone (F3.2.3): un
    selettore per nome/automation_id deve quindi sopravvivere per costruzione, verificato non
    assunto in `tests/test_selector.py::ThemeChangeTests` (incluso un controllo VISIVO reale via
    screenshot, non solo "il flag non ha sollevato")."""
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QStyleFactory

    app.setStyle(QStyleFactory.create("Fusion"))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(230, 230, 230))
    palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.Text, QColor(230, 230, 230))
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(230, 230, 230))
    app.setPalette(palette)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auto-close-after", type=float, default=None,
        help="Chiude la finestra da sola dopo N secondi (per lanci automatizzati/CI, mai per l'uso manuale).",
    )
    parser.add_argument(
        "--dark-theme", action="store_true",
        help="Applica un tema scuro reale (palette Fusion) - per F3.3.7, verificare che i selettori "
        "sopravvivano a un cambio di tema, non solo di geometria.",
    )
    parser.add_argument(
        "--language", choices=sorted(_ADD_BUTTON_LABELS), default="it",
        help="Lingua del bottone 'Aggiungi'/'Add' - per F3.3.7, verificare che un selettore per "
        "automation_id sopravviva alla traduzione mentre uno per nome no.",
    )
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    if args.dark_theme:
        _apply_dark_theme(app)
    window = ComputerUseFixtureWindow(language=args.language)
    window.show()

    if args.auto_close_after is not None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(int(args.auto_close_after * 1000), app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
