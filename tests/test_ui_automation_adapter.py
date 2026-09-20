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
from unittest import mock

import comtypes

from core.computer_use.ui_automation_adapter import (
    AmbiguousWindowError,
    ElementInfo,
    UIAutomationAdapter,
    WindowNotFoundError,
)

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


class FindWindowByTitleContainingTests(unittest.TestCase):
    """F3.7 (adozione, motivata da Esplora File): `find_window_by_title_containing`, per quando
    il titolo COMPLETO include un suffisso dipendente dalla lingua del sistema (es. "cartella -
    Esplora file"/"folder - File Explorer") che `find_window_by_title` (uguaglianza esatta) non
    potrebbe mai prevedere - qui verificato contro la fixture Qt gia' esistente, non serve
    Esplora File per testare questo meccanismo generico."""

    def test_a_substring_that_does_not_exist_raises_within_the_timeout(self):
        adapter = UIAutomationAdapter()
        started = time.monotonic()

        with self.assertRaises(WindowNotFoundError):
            adapter.find_window_by_title_containing("Sottostringa che non esiste XYZ123", timeout_seconds=1.0)

        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 1.0, "deve rispettare davvero il timeout dato, non arrendersi prima")

    def test_finds_the_window_by_a_partial_title(self):
        process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        try:
            adapter = UIAutomationAdapter()
            adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)  # attende che sia visibile

            window = adapter.find_window_by_title_containing("Computer Use Fixture", timeout_seconds=5.0)

            info = adapter.describe_element(window)
            self.assertEqual(info.name, _FIXTURE_WINDOW_TITLE)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def test_an_ambiguous_substring_raises_immediately_not_after_the_full_timeout(self):
        """Due finestre della fixture (una per ogni processo) condividono lo stesso titolo -
        cercare per una sottostringa che le trova entrambe deve fallire SUBITO, non dopo il
        timeout intero (aspettare non risolverebbe mai l'ambiguita', lo stesso principio gia'
        seguito da `SelectorEngine.wait_for_unique_element`, F3.4.7)."""
        process_a = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        process_b = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        try:
            adapter = UIAutomationAdapter()
            # Attende che ENTRAMBE le finestre siano visibili (stesso titolo per entrambe, non
            # distinguibili per processo con find_window_by_process_id data la fragilita' gia'
            # documentata del PID del Popen per questa fixture) - ritenta con un breve intervallo
            # finche' la ricerca non trova davvero l'ambiguita', invece di un singolo tentativo
            # ottimistico che potrebbe vedere solo la prima finestra gia' pronta.
            warmup_deadline = time.monotonic() + 15.0
            while True:
                try:
                    adapter.find_window_by_title_containing("Computer Use Fixture", timeout_seconds=0.2)
                except AmbiguousWindowError:
                    break
                except WindowNotFoundError:
                    pass
                if time.monotonic() > warmup_deadline:
                    self.fail("le due finestre della fixture non sono mai comparse entrambe")

            started = time.monotonic()
            with self.assertRaises(AmbiguousWindowError):
                adapter.find_window_by_title_containing("Computer Use Fixture", timeout_seconds=5.0)

            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 3.0, "l'ambiguita' non deve aspettare il timeout intero")
        finally:
            for process in (process_a, process_b):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


class WaitForNewTopLevelWindowTests(unittest.TestCase):
    """F3.4.7 (adozione - un residuo genuinamente diverso, non gia' coperto dal capstone di
    questo stesso incremento): quel capstone ha trovato che un dialogo MODALE di Qt (`QMessageBox`)
    compare come DISCENDENTE della finestra genitrice nell'albero di CONTROLLO di UI Automation,
    non come figlio del desktop - risolto li' con `SelectorEngine.wait_for_unique_element` cercato
    a partire dalla finestra nota. Il caso che QUESTO rimane a coprire e' diverso: una finestra
    NUOVA e IMPREVISTA che compare come vera finestra di primo livello separata (es. un dialogo
    di sistema, un prompt di un processo diverso) - un titolo non noto in anticipo, quindi mai
    cercabile con `find_window_by_title`/`find_window_by_title_containing`. Verificato contro la
    fixture Qt gia' esistente (nessuna nuova app reale necessaria per testare questo meccanismo
    generico), stesso schema di `FindWindowByTitleContainingTests` sopra."""

    def test_no_new_window_raises_within_the_timeout(self):
        adapter = UIAutomationAdapter()
        baseline = adapter.snapshot_top_level_window_handles()
        started = time.monotonic()

        with self.assertRaises(WindowNotFoundError):
            adapter.wait_for_new_top_level_window(baseline, timeout_seconds=1.0)

        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 1.0, "deve rispettare davvero il timeout dato, non arrendersi prima")

    def test_detects_a_real_new_window_that_appears_after_the_baseline(self):
        adapter = UIAutomationAdapter()
        baseline = adapter.snapshot_top_level_window_handles()

        process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        try:
            window = adapter.wait_for_new_top_level_window(baseline, timeout_seconds=15.0)
            info = adapter.describe_element(window)
            self.assertEqual(info.name, _FIXTURE_WINDOW_TITLE, "deve trovare la finestra nuova per davvero, non una qualunque")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def test_two_new_windows_appearing_together_raise_ambiguous_error(self):
        adapter = UIAutomationAdapter()
        baseline = adapter.snapshot_top_level_window_handles()

        process_a = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        process_b = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        try:
            # Stesso principio di avvio-e-ritenta di FindWindowByTitleContainingTests sopra:
            # attende che ENTRAMBE le finestre siano visibili prima di controllare l'ambiguita',
            # invece di un singolo tentativo ottimistico che potrebbe vedere solo la prima.
            warmup_deadline = time.monotonic() + 15.0
            while True:
                try:
                    adapter.wait_for_new_top_level_window(baseline, timeout_seconds=0.2)
                except AmbiguousWindowError:
                    break
                except WindowNotFoundError:
                    pass
                if time.monotonic() > warmup_deadline:
                    self.fail("le due finestre della fixture non sono mai comparse entrambe")

            started = time.monotonic()
            with self.assertRaises(AmbiguousWindowError):
                adapter.wait_for_new_top_level_window(baseline, timeout_seconds=5.0)

            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 3.0, "l'ambiguita' non deve aspettare il timeout intero")
        finally:
            for process in (process_a, process_b):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


class RetryTransientComErrorTests(unittest.TestCase):
    """`_retry_transient_com_error` (adozione, motivata da un buco reale trovato in CI - vedi il
    suo docstring): a differenza del resto di questo file (dove mockare comtypes/IUIAutomation
    testerebbe solo il mock, non Windows - vedi il docstring del modulo), questo metodo e' pura
    logica di controllo Python (quante volte riprovare, quando arrendersi) - legittimamente
    testabile con una funzione `call` finta, senza mockare alcuna interfaccia COM reale."""

    def test_succeeds_immediately_without_retrying_when_the_first_call_works(self):
        adapter = UIAutomationAdapter()
        call = mock.Mock(return_value="ok")

        result = adapter._retry_transient_com_error(call)

        self.assertEqual(result, "ok")
        self.assertEqual(call.call_count, 1)

    def test_retries_and_recovers_from_a_transient_com_error(self):
        adapter = UIAutomationAdapter()
        transient_error = comtypes.COMError(-2146233083, None, (None, None, None, 0, None))
        call = mock.Mock(side_effect=[transient_error, transient_error, "ok"])

        result = adapter._retry_transient_com_error(call)

        self.assertEqual(result, "ok")
        self.assertEqual(call.call_count, 3)

    def test_gives_up_after_three_attempts_and_reraises_the_last_error(self):
        adapter = UIAutomationAdapter()
        transient_error = comtypes.COMError(-2146233083, None, (None, None, None, 0, None))
        call = mock.Mock(side_effect=[transient_error, transient_error, transient_error])

        with self.assertRaises(comtypes.COMError):
            adapter._retry_transient_com_error(call)

        self.assertEqual(call.call_count, 3, "non deve mai riprovare piu' di 3 volte")

    def test_a_non_com_error_propagates_immediately_without_retrying(self):
        """Solo `comtypes.COMError` e' considerato un transitorio da assorbire - un errore
        Python qualunque (es. un bug reale nel codice chiamante) deve propagare SUBITO, non essere
        nascosto dietro 3 tentativi silenziosi."""
        adapter = UIAutomationAdapter()
        call = mock.Mock(side_effect=ValueError("questo non e' un COMError"))

        with self.assertRaises(ValueError):
            adapter._retry_transient_com_error(call)

        self.assertEqual(call.call_count, 1)


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


class FindMatchingElementsProcessIdTests(_RealFixtureTestCase):
    """F3.3.1 (resto - "app/process", CHIUSO in questo incremento, 20/09/2026): `process_id` in
    `find_matching_elements` - vedi il proprio docstring. Il PID SBAGLIATO usato qui e'
    `os.getpid()` (questo stesso processo di test, Python puro, mai proprietario di alcun elemento
    della fixture Qt) - se il filtro fosse silenziosamente ignorato, questo test lo rivelerebbe
    (troverebbe comunque il bottone).

    **Riusa un buco GIA' noto, non uno nuovo** (vedi `FindWindowByProcessIdTests.
    test_finds_the_real_window_of_a_running_process`, scoperto in un incremento precedente):
    `self.process.pid` (il PID del `Popen`) NON e' il PID reale che possiede la finestra per
    QUESTA fixture - la venv rieseguisce se stessa in un processo figlio su Windows. Il PID VERO,
    coerente con quel test gia' esistente, e' `self.window.CurrentProcessId`."""

    def test_the_real_process_id_of_the_fixture_matches(self):
        real_pid = self.window.CurrentProcessId

        matches = self.adapter.find_matching_elements(self.window, name="Aggiungi", process_id=real_pid)

        self.assertEqual(len(matches), 1)

    def test_a_different_process_id_matches_nothing(self):
        import os

        matches = self.adapter.find_matching_elements(self.window, name="Aggiungi", process_id=os.getpid())

        self.assertEqual(matches, [])

    def test_process_id_alone_is_a_sufficient_criterion(self):
        """A differenza di `window_title_contains` (mai un criterio da solo per `find_matching_
        elements`), `process_id` e' una PROPRIETA' UI Automation vera e propria - cercare "ogni
        elemento di questo processo" senza altri criteri e' una richiesta legittima, non deve
        sollevare il `ValueError` per "nessun criterio dato"."""
        real_pid = self.window.CurrentProcessId

        matches = self.adapter.find_matching_elements(self.window, process_id=real_pid)

        self.assertGreater(len(matches), 0)

    def test_no_criteria_at_all_still_raises(self):
        with self.assertRaises(ValueError):
            self.adapter.find_matching_elements(self.window)


if __name__ == "__main__":
    unittest.main()
