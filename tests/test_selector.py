"""Test unitari per core/computer_use/selector.py (F3.3.1-F3.3.3, prima fetta del selector
engine - mai iniziata prima d'ora). Stesso schema di tests/test_ui_automation_adapter.py: lancia
davvero la fixture di F3.1.1 in un processo separato e cerca elementi con UI Automation vera - un
selettore che "funziona" solo contro un albero finto (ElementInfo costruiti a mano) non
proverebbe che le condizioni COM native (FindAll + CreateAndCondition) sono corrette, il punto
centrale di questo modulo."""
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from comtypes.gen import UIAutomationClient as UIA

from core.computer_use.executor import ActionExecutor
from core.computer_use.selector import AmbiguousSelectionError, ElementSelector, NoMatchError, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter
from tests.test_ui_automation_adapter import _RealFixtureTestCase

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_WINDOW_TITLE = "Jake Computer Use Fixture"


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


class ElementSelectorSerializationTests(unittest.TestCase):
    """F3.3.4 (resto - "salvare selector procedurali senza coordinate assolute"): `to_dict`/
    `from_dict`, un formato di salvataggio semplice (un dict, agnostico rispetto a come il
    chiamante lo persiste davvero - JSON su disco, una riga in un futuro formato di "procedura
    registrata" per F3.8) invece di nessuno."""

    def test_to_dict_omits_criteria_that_were_never_given(self):
        selector = ElementSelector(name="Aggiungi")
        self.assertEqual(selector.to_dict(), {"name": "Aggiungi"})

    def test_to_dict_includes_every_criterion_that_was_given(self):
        selector = ElementSelector(name="Aggiungi", control_type="Button", automation_id="fixture_add_button")
        self.assertEqual(
            selector.to_dict(),
            {"name": "Aggiungi", "control_type": "Button", "automation_id": "fixture_add_button"},
        )

    def test_a_round_trip_through_dict_reproduces_an_identical_selector(self):
        original = ElementSelector(name="Rimuovi selezionato", control_type="Button")
        restored = ElementSelector.from_dict(original.to_dict())
        self.assertEqual(original, restored)

    def test_from_dict_with_no_known_criteria_raises_the_same_error_as_the_constructor(self):
        with self.assertRaises(ValueError):
            ElementSelector.from_dict({})

    def test_from_dict_rejects_an_unknown_key_instead_of_silently_dropping_it(self):
        """Un selettore salvato con un campo scritto male o di uno schema futuro non supportato
        deve fallire RUMOROSAMENTE - ignorarlo silenziosamente produrrebbe un selettore PIU'
        AMPIO di quello originariamente salvato (un criterio perso e' un rischio di match
        ambiguo/sbagliato, non un dettaglio innocuo)."""
        with self.assertRaises(ValueError):
            ElementSelector.from_dict({"nome": "Aggiungi"})  # typo reale: "nome" non "name"


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


class WaitForUniqueElementTests(_RealFixtureTestCase):
    """F3.4.7 (adozione): polling con timeout invece di uno sleep fisso o un singolo tentativo
    ottimistico - verificato sia il caso "appare in ritardo" (il punto centrale del metodo) sia i
    due modi onesti di fallire (nulla entro il timeout, o un'ambiguita' che non aspetta il
    timeout perche' il tempo non la risolverebbe mai)."""

    def setUp(self):
        self.engine = SelectorEngine(self.adapter)
        self.executor = ActionExecutor()

    def tearDown(self):
        reset_button = self.adapter.find_matching_elements(self.window, name="Reset", control_type="Button")[0]
        self.executor.invoke(reset_button)

    def test_returns_immediately_when_the_element_already_exists(self):
        result = self.engine.wait_for_unique_element(
            self.window, ElementSelector(name="Aggiungi", control_type="Button"), timeout_seconds=1.0,
        )

        self.assertIsNotNone(result)

    def test_finds_an_element_that_appears_only_after_a_short_delay(self):
        def _add_item_after_a_delay():
            time.sleep(0.5)
            input_field = self.adapter.find_matching_elements(
                self.window, automation_id="QApplication.jake_fixture_window.fixture_input",
            )[0]
            self.executor.set_value(input_field, "in ritardo")
            add_button = self.adapter.find_matching_elements(self.window, name="Aggiungi", control_type="Button")[0]
            self.executor.invoke(add_button)

        thread = threading.Thread(target=_add_item_after_a_delay)
        thread.start()
        try:
            item = self.engine.wait_for_unique_element(
                self.window, ElementSelector(name="in ritardo", control_type="ListItem"), timeout_seconds=3.0,
            )
        finally:
            thread.join(timeout=5)

        self.assertEqual(item.CurrentName, "in ritardo")

    def test_raises_no_match_after_the_timeout_when_nothing_ever_appears(self):
        started = time.monotonic()

        with self.assertRaises(NoMatchError):
            self.engine.wait_for_unique_element(
                self.window, ElementSelector(name="questo non apparira' mai"), timeout_seconds=1.0,
            )

        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 1.0, "deve rispettare davvero il timeout dato")

    def test_an_ambiguous_match_raises_immediately_not_after_the_full_timeout(self):
        """Le due voci dell'albero condividono lo stesso control_type - aspettare non risolverebbe
        mai l'ambiguita', quindi non ha senso aspettare l'intero timeout prima di dichiararla."""
        started = time.monotonic()

        with self.assertRaises(AmbiguousSelectionError):
            self.engine.wait_for_unique_element(self.window, ElementSelector(control_type="TreeItem"), timeout_seconds=5.0)

        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0, "l'ambiguita' non deve aspettare il timeout intero")


class LocalizationAfterResizeAndMoveTests(unittest.TestCase):
    """F3.3.7 ("testare la localizzazione dopo resize, reorder, traduzione e tema") - prima fetta:
    RESIZE e MOVE reali della finestra, verificati con `TransformPattern` (F3.2, mai usato prima
    d'ora in questo progetto - verificato con un probe dedicato che la fixture Qt lo supporta,
    `CanResize`/`CanMove` entrambi veri, prima di scrivere questo test). Processo fixture DEDICATO
    per classe (non il `_RealFixtureTestCase` condiviso usato sopra) perche' questi test MUTANO la
    geometria della finestra - un side effect che non deve mai fuoriuscire verso altri test che
    assumono le dimensioni/posizione di default."""

    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        self.addCleanup(self._terminate_process)
        self.adapter = UIAutomationAdapter()
        self.engine = SelectorEngine(self.adapter)
        self.window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)

    def _terminate_process(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def _resize_and_move(self, width: int, height: int, x: int, y: int) -> None:
        pattern = self.window.GetCurrentPattern(UIA.UIA_TransformPatternId)
        transform = pattern.QueryInterface(UIA.IUIAutomationTransformPattern)
        transform.Resize(width, height)
        transform.Move(x, y)

    def test_a_name_based_selector_still_finds_the_element_after_a_real_resize_and_move(self):
        before = self.engine.find_unique(self.window, ElementSelector(name="Aggiungi", control_type="Button"))
        bounds_before = before.bounds

        self._resize_and_move(900, 700, 50, 50)
        deadline = time.monotonic() + 3.0
        after = None
        while time.monotonic() < deadline:
            after = self.engine.find_unique(self.window, ElementSelector(name="Aggiungi", control_type="Button"))
            if after.bounds != bounds_before:
                break
            time.sleep(0.1)

        self.assertIsNotNone(after, "il selettore per nome deve continuare a trovare l'elemento dopo il resize")
        self.assertNotEqual(
            after.bounds, bounds_before,
            "le coordinate DEVONO essere cambiate col resize/move reale - altrimenti il test non proverebbe nulla",
        )

    def test_a_click_by_name_still_works_at_the_new_coordinates_after_resize(self):
        """Non basta che il selettore RITROVI l'elemento (test sopra) - deve anche poterci AGIRE
        alle coordinate NUOVE, non a quelle stale lette prima del resize (il buco che questo test
        e' pensato a scoprire se mai riapparisse: un click di F3.4 che usasse coordinate cache
        invece di rileggerle dal vivo dopo un resize fallirebbe silenziosamente contro il bersaglio
        sbagliato)."""
        self._resize_and_move(900, 700, 50, 50)
        # Aspetta che il resize sia davvero applicato (stesso principio a polling di sopra) prima
        # di agire, non uno sleep fisso.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            info = self.adapter.describe_element(self.window)
            if info is not None and info.bounds[2] > 800:
                break
            time.sleep(0.1)

        executor = ActionExecutor()
        input_field = self.engine.find_unique_element(
            self.window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_input"),
        )
        executor.set_value(input_field, "dopo il resize")
        add_button = self.engine.find_unique_element(self.window, ElementSelector(name="Aggiungi", control_type="Button"))
        executor.invoke(add_button)

        item = self.engine.wait_for_unique_element(
            self.window, ElementSelector(name="dopo il resize", control_type="ListItem"), timeout_seconds=3.0,
        )
        self.assertEqual(item.CurrentName, "dopo il resize")


if __name__ == "__main__":
    unittest.main()
