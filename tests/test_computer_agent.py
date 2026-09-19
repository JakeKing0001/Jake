"""Test unitari per il controller unificato dello schermo (v3.7, core/computer_agent.py):
osserva/localizza/clicca/verifica come un'unica pipeline. Nessun vero mouse/schermo: pyautogui e
le funzioni di cattura schermo sono mockate."""
import time
import unittest
from unittest import mock

from PIL import Image

from core.computer_agent import EVIDENCE_NONE, EVIDENCE_PIXEL_DIFF, ComputerAgent
from core.computer_use.executor import ElementNotInteractableError
from core.computer_use.selector import AmbiguousSelectionError, NoMatchError
from core.computer_use.ui_automation_adapter import ElementInfo, WindowNotFoundError

WORDS = [{"text": "Accedi", "line": 0, "x": 100, "y": 200, "w": 60, "h": 20}]

_BUTTON_INFO = ElementInfo(
    name="Aggiungi", automation_id="fixture_add_button", control_type="Button",
    bounds=(100, 200, 60, 20), enabled=True, selected=None, toggle_state=None, focused=False,
)


class ObserveAndLocateTests(unittest.TestCase):
    def test_observe_delegates_to_ocr(self):
        with mock.patch("core.vision.screen.read_screen_words", return_value=WORDS):
            self.assertEqual(ComputerAgent().observe(), WORDS)

    def test_locate_text_reads_words_when_not_given(self):
        with mock.patch("core.vision.screen.read_screen_words", return_value=WORDS):
            hit = ComputerAgent().locate_text("Accedi")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["matched"], "Accedi")

    def test_locate_text_uses_given_words_without_a_fresh_observe(self):
        with mock.patch("core.vision.screen.read_screen_words") as read_words:
            hit = ComputerAgent().locate_text("Accedi", words=WORDS)
        read_words.assert_not_called()
        self.assertIsNotNone(hit)

    def test_locate_text_with_no_words_returns_none(self):
        with mock.patch("core.vision.screen.read_screen_words", return_value=None):
            self.assertIsNone(ComputerAgent().locate_text("Accedi"))


class ClickPointTests(unittest.TestCase):
    def test_reports_verified_when_screen_visibly_changes(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click") as click, mock.patch("time.sleep"):
            result = ComputerAgent().click_point(5, 5, matched="Accedi")
        click.assert_called_once_with(5, 5, button="left")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual(result.matched, "Accedi")
        self.assertGreater(result.change_ratio, 0.9)

    def test_reports_unverified_when_nothing_visibly_changes(self):
        """Un click valido puo' legittimamente non cambiare nulla sullo schermo (link verso una
        pagina gia' aperta): deve restare success=True, solo verified=False. Ricliccare alla
        cieca in automatico qui dentro rischierebbe di attivare due volte un'azione gia'
        riuscita, quindi NON deve succedere: la decisione spetta all'agente a passi."""
        same = Image.new("RGB", (10, 10), (0, 0, 0))
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[same, same.copy()]), \
             mock.patch("pyautogui.click") as click, mock.patch("time.sleep"):
            result = ComputerAgent().click_point(5, 5)
        click.assert_called_once()
        self.assertTrue(result.success)
        self.assertFalse(result.verified)

    def test_click_failure_is_reported_without_raising(self):
        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (10, 10))), \
             mock.patch("pyautogui.click", side_effect=RuntimeError("no display")):
            result = ComputerAgent().click_point(5, 5)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_double_click_uses_double_click_api(self):
        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (10, 10))), \
             mock.patch("pyautogui.doubleClick") as double_click, mock.patch("time.sleep"):
            result = ComputerAgent().click_point(5, 5, button="double")
        double_click.assert_called_once_with(5, 5)
        self.assertTrue(result.success)

    def test_screenshot_capture_failure_still_allows_the_click_to_succeed(self):
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=RuntimeError("no screen")), \
             mock.patch("pyautogui.click") as click:
            result = ComputerAgent().click_point(5, 5)
        click.assert_called_once()
        self.assertTrue(result.success)
        self.assertFalse(result.verified)


class EvidenceStrengthTests(unittest.TestCase):
    """F3.5.5: `evidence` rende esplicita la FONTE del campo `verified` - sempre pixel diff in
    questa classe (mai una verifica basata su stato reale dell'app, quella e' F3.4/F3.5), e mai
    dichiarato quando nessun controllo e' davvero avvenuto."""

    def test_a_completed_pixel_diff_check_is_reported_as_pixel_diff_evidence(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click"), mock.patch("time.sleep"):
            result = ComputerAgent().click_point(5, 5)

        self.assertEqual(result.evidence, EVIDENCE_PIXEL_DIFF)

    def test_an_unverified_click_from_no_visible_change_is_still_pixel_diff_evidence(self):
        """Il controllo E' avvenuto (le due schermate sono state confrontate) - solo non ha
        rilevato un cambiamento. EVIDENCE_PIXEL_DIFF descrive che tipo di controllo e' avvenuto,
        non se ha trovato qualcosa."""
        same = Image.new("RGB", (10, 10), (0, 0, 0))
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[same, same.copy()]), \
             mock.patch("pyautogui.click"), mock.patch("time.sleep"):
            result = ComputerAgent().click_point(5, 5)

        self.assertFalse(result.verified)
        self.assertEqual(result.evidence, EVIDENCE_PIXEL_DIFF)

    def test_no_evidence_at_all_when_the_initial_screenshot_capture_fails(self):
        """Nessun controllo e' stato davvero possibile - EVIDENCE_NONE, mai EVIDENCE_PIXEL_DIFF
        indovinato per un confronto che non e' mai avvenuto."""
        with mock.patch("core.vision.screen.capture_screenshot_image", side_effect=RuntimeError("no screen")), \
             mock.patch("pyautogui.click"):
            result = ComputerAgent().click_point(5, 5)

        self.assertEqual(result.evidence, EVIDENCE_NONE)


class ClickTextTests(unittest.TestCase):
    def test_not_found_never_touches_the_mouse(self):
        with mock.patch("core.vision.screen.read_screen_words", return_value=[]), mock.patch("pyautogui.click") as click:
            result = ComputerAgent().click_text("Non c'e'")
        click.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_found_locates_then_clicks(self):
        with mock.patch("core.vision.screen.read_screen_words", return_value=WORDS), \
             mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (10, 10))), \
             mock.patch("pyautogui.click") as click, mock.patch("time.sleep"):
            result = ComputerAgent().click_text("Accedi")
        click.assert_called_once_with(130, 210, button="left")
        self.assertTrue(result.success)
        self.assertEqual(result.matched, "Accedi")


class ClickElementTests(unittest.TestCase):
    """F3.4.2: click_element trova un elemento via UI Automation (F3.2/F3.3) e lo clicca tramite
    Invoke (F3.4), con ripiego a un click pixel se Invoke non funziona (F3.5) - nessuna
    dipendenza da uno schermo reale, ogni pezzo della catena e' mockato al proprio punto di
    ingresso, come gia' fatto per il resto di questa classe."""

    def _mocked_adapter_and_engine(self, MockAdapter, MockEngine, *, element=mock.sentinel.element):
        adapter = MockAdapter.return_value
        adapter.find_window_by_title.return_value = mock.sentinel.window
        adapter.describe_element.return_value = _BUTTON_INFO
        engine = MockEngine.return_value
        engine.wait_for_unique_element.return_value = element
        return adapter, engine

    def test_window_not_found_is_reported_without_touching_the_mouse(self):
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("pyautogui.click") as click:
            MockAdapter.return_value.find_window_by_title.side_effect = WindowNotFoundError("no window")
            result = ComputerAgent().click_element(window_title="Non esiste", name="Aggiungi")

        click.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")

    def test_no_match_is_reported_without_touching_the_mouse(self):
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("pyautogui.click") as click:
            MockAdapter.return_value.find_window_by_title.return_value = mock.sentinel.window
            MockEngine.return_value.wait_for_unique_element.side_effect = NoMatchError("nessuno")
            result = ComputerAgent().click_element(window_title="Jake Computer Use Fixture", name="Non c'e'")

        click.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_an_ambiguous_match_is_reported_without_touching_the_mouse(self):
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("pyautogui.click") as click:
            MockAdapter.return_value.find_window_by_title.return_value = mock.sentinel.window
            MockEngine.return_value.wait_for_unique_element.side_effect = AmbiguousSelectionError("troppi")
            result = ComputerAgent().click_element(window_title="Jake Computer Use Fixture", control_type="Button")

        click.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "AMBIGUOUS_MATCH")

    def test_a_successful_invoke_with_a_visible_change_reports_verified(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click") as click, mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            result = ComputerAgent().click_element(window_title="Jake Computer Use Fixture", name="Aggiungi")

        MockExecutor.return_value.invoke.assert_called_once_with(mock.sentinel.element)
        click.assert_not_called()
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual(result.evidence, EVIDENCE_PIXEL_DIFF)
        self.assertEqual((result.x, result.y), (130, 210), "il centro deve venire dai bounds letti via UI Automation")

    def test_a_successful_invoke_without_any_visible_change_still_reports_success(self):
        """Stessa distinzione gia' seguita da click_point (F3.5.5): un'azione davvero eseguita
        che non cambia nulla di visibile (es. un bottone il cui effetto non e' visivo) resta
        success=True, solo verified=False - mai trattata come un fallimento."""
        same = Image.new("RGB", (10, 10), (0, 0, 0))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[same, same.copy(), same.copy()]), \
             mock.patch("pyautogui.click") as click, mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            result = ComputerAgent().click_element(window_title="Jake Computer Use Fixture", name="Aggiungi")

        self.assertTrue(MockExecutor.return_value.invoke.called)
        # Senza verifica, la scala tenta anche il ripiego pixel (verify() fallisce dopo invoke).
        click.assert_called_once_with(130, 210)
        self.assertTrue(result.success)
        self.assertFalse(result.verified)

    def test_invoke_failing_falls_back_to_a_pixel_click_at_the_same_coordinates(self):
        """`invoke()` che SOLLEVA non chiama mai `verify()` per quel tentativo (F3.5.3, "verify()
        dopo l'azione" - un'azione mai eseguita non ha nulla da verificare) - solo il ripiego
        pixel_click produce una cattura schermo "dopo", oltre a quella "prima" iniziale."""
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click") as click, mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            MockExecutor.return_value.invoke.side_effect = ElementNotInteractableError("disabilitato")
            result = ComputerAgent().click_element(window_title="Jake Computer Use Fixture", name="Aggiungi")

        click.assert_called_once_with(130, 210)
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    def test_a_bounds_read_failure_is_reported_without_touching_the_mouse(self):
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("pyautogui.click") as click:
            MockAdapter.return_value.find_window_by_title.return_value = mock.sentinel.window
            MockAdapter.return_value.describe_element.return_value = None
            MockEngine.return_value.wait_for_unique_element.return_value = mock.sentinel.element
            result = ComputerAgent().click_element(window_title="Jake Computer Use Fixture", name="Aggiungi")

        click.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_root_bypasses_the_window_title_lookup_entirely(self):
        """F3.6 (adozione): un browser non ha un titolo di finestra prevedibile in anticipo (F3.6,
        `core/computer_use/browser_adapter.py`) - passare `root` (un elemento gia' risolto, es.
        il nodo `Document` di una pagina) deve saltare del tutto `find_window_by_title`, non
        chiamarlo comunque con un valore vuoto."""
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click") as click, mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            result = ComputerAgent().click_element(root=mock.sentinel.document, name="Aggiungi")

        MockAdapter.return_value.find_window_by_title.assert_not_called()
        MockEngine.return_value.wait_for_unique_element.assert_called_once_with(
            mock.sentinel.document, mock.ANY, timeout_seconds=5.0,
        )
        MockExecutor.return_value.invoke.assert_called_once_with(mock.sentinel.element)
        click.assert_not_called()
        self.assertTrue(result.success)

    def test_neither_window_title_nor_root_raises_a_clear_error(self):
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter"):
            with self.assertRaises(ValueError):
                ComputerAgent().click_element(name="Aggiungi")


class TypeIntoElementTests(unittest.TestCase):
    """F3.4.2 (resto): type_into_element - stessa struttura di ClickElementTests sopra (fattorizzata
    da `_locate_element_center`, condivisa da entrambi i metodi), qui con SetValue/pixel_type
    invece di Invoke/pixel_click."""

    def _mocked_adapter_and_engine(self, MockAdapter, MockEngine, *, element=mock.sentinel.element):
        adapter = MockAdapter.return_value
        adapter.find_window_by_title.return_value = mock.sentinel.window
        adapter.describe_element.return_value = _BUTTON_INFO
        engine = MockEngine.return_value
        engine.wait_for_unique_element.return_value = element
        return adapter, engine

    def test_window_not_found_is_reported_without_touching_the_keyboard(self):
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("pyautogui.write") as write:
            MockAdapter.return_value.find_window_by_title.side_effect = WindowNotFoundError("no window")
            result = ComputerAgent().type_into_element("un segreto", window_title="Non esiste")

        write.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")

    def test_no_match_is_reported_without_touching_the_keyboard(self):
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("pyautogui.write") as write:
            MockAdapter.return_value.find_window_by_title.return_value = mock.sentinel.window
            MockEngine.return_value.wait_for_unique_element.side_effect = NoMatchError("nessuno")
            result = ComputerAgent().type_into_element("un segreto", window_title="Jake Computer Use Fixture", name="Non c'e'")

        write.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_a_successful_set_value_with_a_visible_change_reports_verified(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.write") as write, mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            result = ComputerAgent().type_into_element("un segreto", window_title="Jake Computer Use Fixture", name="Campo")

        MockExecutor.return_value.set_value.assert_called_once_with(mock.sentinel.element, "un segreto")
        write.assert_not_called()
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    def test_set_value_failing_falls_back_to_a_real_click_and_typewrite(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click") as click, mock.patch("pyautogui.hotkey") as hotkey, \
             mock.patch("pyautogui.write") as write, mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            MockExecutor.return_value.set_value.side_effect = ElementNotInteractableError("disabilitato")
            result = ComputerAgent().type_into_element("un segreto", window_title="Jake Computer Use Fixture", name="Campo")

        click.assert_called_once_with(130, 210)
        hotkey.assert_called_once_with("ctrl", "a")
        write.assert_called_once_with("un segreto")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    def test_the_pixel_fallback_selects_all_before_writing_so_it_never_duplicates_a_silent_uia_success(self):
        """Buco reale trovato verificando questo metodo contro la fixture, non ipotizzato: SetValue
        (F3.4) puo' riuscire per davvero (il campo cambia sul serio) mentre l'evidenza debole del
        pixel diff (F3.5.5, calcolata sull'INTERO schermo) non rileva il cambiamento in un campo
        piccolo e fa scattare comunque il ripiego pixel - senza Ctrl+A prima, `pyautogui.write`
        si limiterebbe ad AGGIUNGERE il testo a quello gia' impostato da SetValue, duplicandolo
        (verificato contro la fixture vera: il campo conteneva davvero il testo raddoppiato prima
        di questa correzione)."""
        same = Image.new("RGB", (10, 10), (0, 0, 0))
        call_order = []
        manager = mock.Mock()
        manager.click.side_effect = lambda *a, **k: call_order.append("click")
        manager.hotkey.side_effect = lambda *a, **k: call_order.append("hotkey")
        manager.write.side_effect = lambda *a, **k: call_order.append("write")
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[same, same.copy(), same.copy()]), \
             mock.patch("pyautogui.click", manager.click), mock.patch("pyautogui.hotkey", manager.hotkey), \
             mock.patch("pyautogui.write", manager.write), mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            # SetValue "riesce" (non solleva) ma l'evidenza debole non rileva alcun cambiamento -
            # esattamente il caso reale che ha motivato questa correzione.
            result = ComputerAgent().type_into_element("un segreto", window_title="Jake Computer Use Fixture", name="Campo")

        self.assertTrue(MockExecutor.return_value.set_value.called, "il ripiego pixel deve scattare comunque quando l'evidenza non conferma")
        self.assertEqual(call_order, ["click", "hotkey", "write"], "Ctrl+A deve precedere la scrittura, non solo essere chiamato")
        manager.write.assert_called_once_with("un segreto")
        self.assertTrue(result.success)

    def test_the_typed_text_never_appears_in_the_result(self):
        """Lo stesso principio gia' seguito da ElementActionReceipt.set_value (F3.4.5): un campo
        testo libero nel risultato rischierebbe di far finire una password in una struttura
        loggabile."""
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor"), \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.write"), mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            result = ComputerAgent().type_into_element("hunter2_super_secret", window_title="Jake Computer Use Fixture", name="Campo")

        self.assertNotIn("hunter2_super_secret", str(result))


class IdempotencyKeyTests(unittest.TestCase):
    """F3.4.6 ("evitare doppia esecuzione sui retry"): `idempotency_key` su click_element/
    type_into_element - una cache PER ISTANZA (vedi ComputerAgent.__init__) che restituisce il
    risultato gia' ottenuto invece di rieseguire l'azione quando la stessa chiave e' ancora
    valida. Stessa infrastruttura mockata di ClickElementTests/TypeIntoElementTests sopra."""

    def _mocked_adapter_and_engine(self, MockAdapter, MockEngine, *, element=mock.sentinel.element):
        adapter = MockAdapter.return_value
        adapter.find_window_by_title.return_value = mock.sentinel.window
        adapter.describe_element.return_value = _BUTTON_INFO
        engine = MockEngine.return_value
        engine.wait_for_unique_element.return_value = element
        return adapter, engine

    def test_a_repeated_key_after_a_successful_click_returns_the_cached_result_without_clicking_again(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click"), mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            agent = ComputerAgent()
            first = agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", idempotency_key="passo-42")
            second = agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", idempotency_key="passo-42")

        MockExecutor.return_value.invoke.assert_called_once_with(mock.sentinel.element)
        MockAdapter.return_value.find_window_by_title.assert_called_once()
        self.assertEqual(first, second)
        self.assertTrue(second.success)

    def test_without_a_key_every_call_clicks_again_unchanged_default_behavior(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch(
                 "core.vision.screen.capture_screenshot_image",
                 side_effect=[before, after, before.copy(), after.copy()],
             ), \
             mock.patch("pyautogui.click"), mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            agent = ComputerAgent()
            agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi")
            agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi")

        self.assertEqual(MockExecutor.return_value.invoke.call_count, 2)

    def test_a_failed_lookup_is_never_cached_so_a_retry_with_the_same_key_tries_again(self):
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("pyautogui.click") as click:
            MockAdapter.return_value.find_window_by_title.return_value = mock.sentinel.window
            MockEngine.return_value.wait_for_unique_element.side_effect = NoMatchError("nessuno")
            agent = ComputerAgent()
            first = agent.click_element(window_title="Jake Computer Use Fixture", name="Non c'e'", idempotency_key="passo-7")
            second = agent.click_element(window_title="Jake Computer Use Fixture", name="Non c'e'", idempotency_key="passo-7")

        click.assert_not_called()
        self.assertFalse(first.success)
        self.assertFalse(second.success)
        self.assertEqual(MockEngine.return_value.wait_for_unique_element.call_count, 2, "un fallimento non deve mai bloccare un retry legittimo")

    def test_an_expired_key_is_treated_as_a_miss_and_clicks_again(self):
        """Nessun `mock.patch("time.sleep")` qui, a differenza degli altri test di questa classe:
        patcherebbe l'UNICO oggetto modulo `time` condiviso da tutto il processo, rendendo un
        vero `time.sleep(...)` in questo stesso test un no-op e impedendo di osservare una
        scadenza reale della cache - la TTL brevissima (0.05s) tiene comunque il costo aggiuntivo
        di un'attesa vera minimo."""
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch(
                 "core.vision.screen.capture_screenshot_image",
                 side_effect=[before, after, before.copy(), after.copy()],
             ), \
             mock.patch("pyautogui.click"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            agent = ComputerAgent(idempotency_ttl_seconds=0.05)
            agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", idempotency_key="passo-scaduto")
            time.sleep(0.15)
            agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", idempotency_key="passo-scaduto")

        self.assertEqual(MockExecutor.return_value.invoke.call_count, 2, "una chiave scaduta non deve piu' fare da cache")

    def test_different_keys_never_collide_with_each_other(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch(
                 "core.vision.screen.capture_screenshot_image",
                 side_effect=[before, after, before.copy(), after.copy()],
             ), \
             mock.patch("pyautogui.click"), mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            agent = ComputerAgent()
            agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", idempotency_key="a")
            agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", idempotency_key="b")

        self.assertEqual(MockExecutor.return_value.invoke.call_count, 2)

    def test_a_repeated_key_works_the_same_way_for_type_into_element(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.write") as write, mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            agent = ComputerAgent()
            first = agent.type_into_element("un segreto", window_title="Jake Computer Use Fixture", name="Campo", idempotency_key="passo-9")
            second = agent.type_into_element("un segreto", window_title="Jake Computer Use Fixture", name="Campo", idempotency_key="passo-9")

        MockExecutor.return_value.set_value.assert_called_once_with(mock.sentinel.element, "un segreto")
        write.assert_not_called()
        self.assertEqual(first, second)

    def test_the_cached_result_is_an_independent_copy_mutating_it_does_not_corrupt_the_cache(self):
        before = Image.new("RGB", (10, 10), (0, 0, 0))
        after = Image.new("RGB", (10, 10), (255, 255, 255))
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=[before, after]), \
             mock.patch("pyautogui.click"), mock.patch("time.sleep"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            agent = ComputerAgent()
            first = agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", idempotency_key="passo-mut")
            first.matched = "manomesso"
            second = agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", idempotency_key="passo-mut")

        self.assertEqual(second.matched, "Aggiungi")
        MockExecutor.return_value.invoke.assert_called_once()


class PolicyIntegrationTests(unittest.TestCase):
    """F3.4.3 ("richiedere policy prima di upload, submit, send, delete e purchase"):
    `risk_intent`/`policy_parameters`/`automated` su `click_element`/`type_into_element`. Usa un
    vero `PolicyEngine` (costruzione economica, nessun mock del motore stesso - solo dell'adapter/
    selector/executor UI Automation, come il resto di questa classe) cosi' i test esercitano la
    LOGICA DI DECISIONE reale, non un doppio che si limita a restituire cio' che gli si dice."""

    def _mocked_adapter_and_engine(self, MockAdapter, MockEngine, *, element=mock.sentinel.element):
        adapter = MockAdapter.return_value
        adapter.find_window_by_title.return_value = mock.sentinel.window
        adapter.describe_element.return_value = _BUTTON_INFO
        engine = MockEngine.return_value
        engine.wait_for_unique_element.return_value = element
        return adapter, engine

    def test_no_risk_intent_skips_policy_entirely_even_with_a_policy_engine_attached(self):
        """Comportamento IDENTICO a prima di F3.4.3: un `policy_engine` collegato ma nessun
        `risk_intent` dato non deve MAI bloccare nulla - l'opt-in e' sul CHIAMANTE che dichiara il
        rischio, non sulla sola presenza di un motore."""
        from core.policy_engine import PolicyEngine

        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=Exception("nessuno screenshot")), \
             mock.patch("pyautogui.click"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            engine = PolicyEngine(blocked_intents={"DELETE_PATH"})
            agent = ComputerAgent(policy_engine=engine)
            result = agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi")

        self.assertTrue(result.success)
        MockExecutor.return_value.invoke.assert_called_once()

    def test_a_risk_intent_without_a_policy_engine_attached_is_never_checked(self):
        """`risk_intent` da solo, senza `policy_engine` collegato, non deve bloccare nulla - un
        `ComputerAgent()` senza motore non puo' verificare, non deve fallire per un pre-requisito
        mancante (comportamento IDENTICO a prima di F3.4.3 per ogni chiamante che non collega un
        motore, es. `skills/screen_click.py`)."""
        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=Exception("nessuno screenshot")), \
             mock.patch("pyautogui.click"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            agent = ComputerAgent()
            result = agent.click_element(window_title="Jake Computer Use Fixture", name="Aggiungi", risk_intent="DELETE_PATH")

        self.assertTrue(result.success)
        MockExecutor.return_value.invoke.assert_called_once()

    def test_a_blocked_intent_is_refused_without_ever_touching_the_mouse(self):
        from core.policy_engine import PolicyEngine

        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("pyautogui.click") as click:
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            engine = PolicyEngine(blocked_intents={"DELETE_PATH"})
            agent = ComputerAgent(policy_engine=engine)
            result = agent.click_element(window_title="Jake Computer Use Fixture", name="Elimina", risk_intent="DELETE_PATH")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "POLICY_BLOCKED")
        click.assert_not_called()
        MockExecutor.return_value.invoke.assert_not_called()
        MockAdapter.return_value.find_window_by_title.assert_not_called()

    def test_an_intent_needing_confirmation_is_refused_without_a_confirmed_signal(self):
        from core.policy_engine import PolicyEngine

        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("pyautogui.click") as click:
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            engine = PolicyEngine(always_confirm_intents={"DELETE_PATH"})
            agent = ComputerAgent(policy_engine=engine)
            result = agent.click_element(window_title="Jake Computer Use Fixture", name="Elimina", risk_intent="DELETE_PATH")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        click.assert_not_called()
        MockExecutor.return_value.invoke.assert_not_called()

    def test_a_confirmed_signal_allows_the_same_intent_to_proceed(self):
        """La busta di conferma - `policy_parameters={"confirmed": True}` - e' la STESSA che
        `JakeCore` gia' costruisce per ogni altro intent, non un formato nuovo per questo modulo."""
        from core.policy_engine import PolicyEngine

        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=Exception("nessuno screenshot")), \
             mock.patch("pyautogui.click"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            engine = PolicyEngine(always_confirm_intents={"DELETE_PATH"})
            agent = ComputerAgent(policy_engine=engine)
            result = agent.click_element(
                window_title="Jake Computer Use Fixture", name="Elimina", risk_intent="DELETE_PATH",
                policy_parameters={"confirmed": True},
            )

        self.assertTrue(result.success)
        MockExecutor.return_value.invoke.assert_called_once()

    def test_automated_strips_a_forged_confirmed_signal_instead_of_trusting_it(self):
        """Il bug reale numero 1 gia' documentato nel modulo docstring di core/policy_engine.py
        ("un piano automatico poteva auto-autorizzarsi"): `automated=True` deve IGNORARE un
        `confirmed=True` - nessun utente reale e' li' a concederlo in un contesto automatico -
        restando bloccato su CONFIRMATION_REQUIRED invece di procedere."""
        from core.policy_engine import PolicyEngine

        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("pyautogui.click") as click:
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            engine = PolicyEngine(always_confirm_intents={"DELETE_PATH"})
            agent = ComputerAgent(policy_engine=engine)
            result = agent.click_element(
                window_title="Jake Computer Use Fixture", name="Elimina", risk_intent="DELETE_PATH",
                policy_parameters={"confirmed": True}, automated=True,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        click.assert_not_called()
        MockExecutor.return_value.invoke.assert_not_called()

    def test_type_into_element_checks_policy_before_writing_anything(self):
        from core.policy_engine import PolicyEngine

        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("pyautogui.write") as write:
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            engine = PolicyEngine(blocked_intents={"SEND_EMAIL"})
            agent = ComputerAgent(policy_engine=engine)
            result = agent.type_into_element(
                "un messaggio", window_title="Jake Computer Use Fixture", name="Campo", risk_intent="SEND_EMAIL",
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "POLICY_BLOCKED")
        write.assert_not_called()
        MockExecutor.return_value.set_value.assert_not_called()

    def test_an_idempotency_cache_hit_never_rechecks_policy(self):
        """Un'azione GIA' avvenuta con successo (in cache, F3.4.6) era gia' stata autorizzata a
        suo tempo - ricontrollare la policy per un'azione che non sta per accadere davvero non
        avrebbe senso. Verificato con uno spy sul motore: `decide_interactive` deve essere
        chiamato SOLO alla prima esecuzione, mai alla seconda (cache HIT)."""
        from core.policy_engine import PolicyEngine

        with mock.patch("core.computer_use.ui_automation_adapter.UIAutomationAdapter") as MockAdapter, \
             mock.patch("core.computer_use.selector.SelectorEngine") as MockEngine, \
             mock.patch("core.computer_use.executor.ActionExecutor") as MockExecutor, \
             mock.patch("core.vision.screen.capture_screenshot_image", side_effect=Exception("nessuno screenshot")), \
             mock.patch("pyautogui.click"):
            self._mocked_adapter_and_engine(MockAdapter, MockEngine)
            engine = PolicyEngine(always_confirm_intents={"DELETE_PATH"})
            decide_spy = mock.Mock(wraps=engine.decide_interactive)
            engine.decide_interactive = decide_spy
            agent = ComputerAgent(policy_engine=engine)
            agent.click_element(
                window_title="Jake Computer Use Fixture", name="Elimina", risk_intent="DELETE_PATH",
                policy_parameters={"confirmed": True}, idempotency_key="passo-elimina",
            )
            agent.click_element(
                window_title="Jake Computer Use Fixture", name="Elimina", risk_intent="DELETE_PATH",
                policy_parameters={"confirmed": True}, idempotency_key="passo-elimina",
            )

        self.assertEqual(decide_spy.call_count, 1, "la seconda chiamata deve venire dalla cache, mai ricontrollare la policy")
        MockExecutor.return_value.invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
