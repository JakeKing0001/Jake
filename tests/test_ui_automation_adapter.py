"""Test unitari per core/computer_use/ui_automation_adapter.py (F3.2.1-F3.2.3, prima fetta di
F3.2 - Windows UI Automation adapter, mai iniziata prima d'ora). A differenza della maggior parte
della suite (che evita dipendenze esterne pesanti - Ollama, un microfono reale - per restare
veloce e deterministica), questi test lanciano DAVVERO benchmarks/computer_use_fixture.py in un
processo separato e lo interrogano con UI Automation vera: non c'e' altro modo onesto di
verificare che un adapter COM funzioni contro un provider reale - mockare comtypes/IUIAutomation
testerebbe solo che il mock si comporta come ci si aspetta, non che Windows lo fa (lo stesso
principio "buco reale, non ipotizzato" gia' seguito in questa sessione ha gia' trovato due bug
veri proprio scrivendo questi test, non a tavolino - vedi il modulo). Serve solo una sessione
desktop Windows (niente Ollama/microfono), la stessa cosa gia' richiesta dai test Qt esistenti
(tests/test_hud_*.py) e dal job CI che compila ed esegue il prototipo HUD nativo."""
import subprocess
import sys
import time
import unittest
from pathlib import Path

from core.computer_use.ui_automation_adapter import ElementInfo, UIAutomationAdapter, WindowNotFoundError

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_WINDOW_TITLE = "Jake Computer Use Fixture"


def _find_by_name(info: ElementInfo, name: str, control_type: str | None = None) -> ElementInfo | None:
    if info.name == name and (control_type is None or info.control_type == control_type):
        return info
    for child in info.children:
        found = _find_by_name(child, name, control_type)
        if found is not None:
            return found
    return None


class FindWindowByTitleTests(unittest.TestCase):
    def test_a_window_that_does_not_exist_raises_within_the_timeout(self):
        adapter = UIAutomationAdapter()
        started = time.monotonic()

        with self.assertRaises(WindowNotFoundError):
            adapter.find_window_by_title("Finestra che non esiste per davvero XYZ123", timeout_seconds=1.0)

        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 1.0, "deve rispettare davvero il timeout dato, non arrendersi prima")
        self.assertLess(elapsed, 4.0, "non deve restare bloccato molto oltre il timeout dichiarato")


class FindWindowByProcessIdTests(unittest.TestCase):
    """F3.6 (adozione): `find_window_by_process_id`, motivato da un browser il cui titolo di
    finestra e' imprevedibile in anticipo (vedi `core/computer_use/browser_adapter.py`) - stessa
    logica di `find_window_by_title` sopra, qui verificata contro un PID vero (la fixture Qt gia'
    esistente, non serve un browser per testare questo meccanismo generico)."""

    def test_a_process_id_that_does_not_exist_raises_within_the_timeout(self):
        adapter = UIAutomationAdapter()
        started = time.monotonic()

        with self.assertRaises(WindowNotFoundError):
            adapter.find_window_by_process_id(999_999_999, timeout_seconds=1.0)

        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 1.0, "deve rispettare davvero il timeout dato, non arrendersi prima")

    def test_finds_the_real_window_of_a_running_process(self):
        """Buco reale trovato scrivendo questo test, non ipotizzato: `subprocess.Popen(...).pid`
        NON e' affidabile come "il PID che possiede la finestra" per QUESTA fixture - `python -m
        benchmarks.computer_use_fixture` puo' rieseguirsi in un processo figlio (il launcher della
        venv), lasciando il PID del `Popen` diverso da quello riportato da UI Automation per la
        finestra reale (verificato: numeri diversi, non un'assunzione). Per un browser lanciato
        direttamente (es. `msedge.exe`, il caso motivante di questo metodo - vedi
        `core/computer_use/browser_adapter.py`) i due PID COINCIDONO; qui il test verifica la
        CORRETTEZZA del metodo usando il PID gia' confermato dalla finestra stessa (via
        `find_window_by_title`), non l'assunzione fragile sul PID del `Popen`."""
        process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        try:
            adapter = UIAutomationAdapter()
            window_by_title = adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)
            real_pid = window_by_title.CurrentProcessId

            window_by_pid = adapter.find_window_by_process_id(real_pid, timeout_seconds=5.0)

            info = adapter.describe_element(window_by_pid)
            self.assertEqual(info.name, _FIXTURE_WINDOW_TITLE, "deve trovare la finestra del PID dato, non una qualunque")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


class _RealFixtureTestCase(unittest.TestCase):
    """Un solo processo fixture condiviso da TUTTI i test di questa classe (nessuno muta lo stato
    della fixture, tutti sono read-only - lanciarne uno per test sarebbe solo piu' lento senza
    alcun beneficio)."""

    @classmethod
    def setUpClass(cls):
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "60"],
            cwd=str(_REPO_ROOT),
        )
        cls.adapter = UIAutomationAdapter()
        try:
            cls.window = cls.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)
        except Exception:
            cls._terminate_process()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._terminate_process()

    @classmethod
    def _terminate_process(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait()


class DescribeRealFixtureTests(_RealFixtureTestCase):
    def test_the_window_itself_is_described_correctly(self):
        info = self.adapter.describe_element(self.window)

        self.assertEqual(info.name, _FIXTURE_WINDOW_TITLE)
        self.assertEqual(info.control_type, "Window")
        self.assertTrue(info.enabled)

    def test_the_add_button_is_found_with_the_right_control_type_and_automation_id(self):
        tree = self.adapter.describe_tree(self.window, max_depth=6)

        add_button = _find_by_name(tree, "Aggiungi")

        self.assertIsNotNone(add_button)
        self.assertEqual(add_button.control_type, "Button")
        self.assertTrue(add_button.automation_id.endswith("fixture_add_button"), add_button.automation_id)
        self.assertTrue(add_button.enabled)

    def test_the_remove_button_is_disabled_without_a_selection(self):
        """Lo stesso stato gia' verificato in tests/test_computer_use_fixture.py a livello Qt -
        qui verificato che arrivi davvero fino a UI Automation, non solo dentro il processo Qt."""
        tree = self.adapter.describe_tree(self.window, max_depth=6)

        remove_button = _find_by_name(tree, "Rimuovi selezionato")

        self.assertIsNotNone(remove_button)
        self.assertFalse(remove_button.enabled)

    def test_tab_items_expose_their_selected_state(self):
        tree = self.adapter.describe_tree(self.window, max_depth=8)

        tab_one = _find_by_name(tree, "Tab 1", control_type="TabItem")
        tab_two = _find_by_name(tree, "Tab 2", control_type="TabItem")

        self.assertIsNotNone(tab_one)
        self.assertIsNotNone(tab_two)
        self.assertTrue(tab_one.selected, "Tab 1 e' attiva per default")
        self.assertFalse(tab_two.selected)

    def test_a_button_does_not_support_the_selection_pattern(self):
        """None (non False) - un bottone non supporta affatto il pattern SelectionItem, la
        domanda 'e' selezionato?' non ha senso per lui (vedi il docstring di
        UIAutomationAdapter._selection_state_of)."""
        tree = self.adapter.describe_tree(self.window, max_depth=6)

        add_button = _find_by_name(tree, "Aggiungi")

        self.assertIsNone(add_button.selected)

    def test_tree_items_are_present_in_the_nested_structure(self):
        tree = self.adapter.describe_tree(self.window, max_depth=8)

        category_a = _find_by_name(tree, "Categoria A")

        self.assertIsNotNone(category_a)
        self.assertEqual(category_a.control_type, "TreeItem")

    def test_max_depth_zero_returns_only_the_root_with_no_children(self):
        info = self.adapter.describe_tree(self.window, max_depth=0)

        self.assertEqual(info.name, _FIXTURE_WINDOW_TITLE)
        self.assertEqual(info.children, ())

    def test_a_deep_enough_walk_never_crashes_on_native_window_chrome(self):
        """Buco reale trovato scrivendo questi test, non ipotizzato: la barra del titolo/il
        System Menu hanno provider UI Automation incompleti - un elemento del genere non deve
        far crashare l'intera camminata (vedi il docstring di describe_tree)."""
        tree = self.adapter.describe_tree(self.window, max_depth=8)

        self.assertIsNotNone(tree)
        self.assertGreater(len(tree.children), 0)


if __name__ == "__main__":
    unittest.main()
