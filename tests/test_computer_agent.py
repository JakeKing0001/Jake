"""Test unitari per il controller unificato dello schermo (v3.7, core/computer_agent.py):
osserva/localizza/clicca/verifica come un'unica pipeline. Nessun vero mouse/schermo: pyautogui e
le funzioni di cattura schermo sono mockate."""
import unittest
from unittest import mock

from PIL import Image

from core.computer_agent import EVIDENCE_NONE, EVIDENCE_PIXEL_DIFF, ComputerAgent

WORDS = [{"text": "Accedi", "line": 0, "x": 100, "y": 200, "w": 60, "h": 20}]


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


if __name__ == "__main__":
    unittest.main()
