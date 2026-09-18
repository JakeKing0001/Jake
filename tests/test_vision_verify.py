"""Test unitari per core/computer_use/vision_verify.py (F3.5.1, il gradino "OCR" della scala di
ripiego, prima fetta - mai costruito prima d'ora). Il meccanismo generico e' testato con finti
deterministici (adapter/OCR mockati, nessuna dipendenza da uno schermo reale) - un test contro la
fixture VERA (`tests/test_computer_use_integration.py`, non qui) dimostra il caso concreto che ha
motivato questo modulo: Task 3/10 di F3.1.2 ("espandi categoria"), bloccato per UI Automation ma
verificabile via OCR."""
import unittest
from unittest import mock

from PIL import Image

from core.computer_use.ui_automation_adapter import ElementInfo
from core.computer_use.vision_verify import word_visible_in_window

_WINDOW_INFO = ElementInfo(
    name="Finestra", automation_id="win", control_type="Window",
    bounds=(10, 20, 100, 50), enabled=True, selected=None, toggle_state=None, focused=True,
)


class WordVisibleInWindowTests(unittest.TestCase):
    def test_returns_true_when_the_exact_word_is_among_the_ocr_results(self):
        adapter = mock.Mock()
        adapter.describe_element.return_value = _WINDOW_INFO
        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (200, 200))), \
             mock.patch("core.vision.screen.read_screen_words", return_value=[{"text": "Elemento", "line": 0, "x": 0, "y": 0, "w": 1, "h": 1}]):
            self.assertTrue(word_visible_in_window(adapter, mock.sentinel.window, "Elemento"))

    def test_returns_false_when_the_word_is_not_among_the_ocr_results(self):
        adapter = mock.Mock()
        adapter.describe_element.return_value = _WINDOW_INFO
        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (200, 200))), \
             mock.patch("core.vision.screen.read_screen_words", return_value=[{"text": "Aggiungi", "line": 0, "x": 0, "y": 0, "w": 1, "h": 1}]):
            self.assertFalse(word_visible_in_window(adapter, mock.sentinel.window, "Elemento"))

    def test_a_substring_match_is_not_enough_only_an_exact_word_counts(self):
        """'Elemento' non deve corrispondere a 'ElementoLungo' - una sottostringa non e' la
        stessa parola, eviterebbe falsi positivi su un'etichetta simile ma diversa."""
        adapter = mock.Mock()
        adapter.describe_element.return_value = _WINDOW_INFO
        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (200, 200))), \
             mock.patch("core.vision.screen.read_screen_words", return_value=[{"text": "ElementoLungo", "line": 0, "x": 0, "y": 0, "w": 1, "h": 1}]):
            self.assertFalse(word_visible_in_window(adapter, mock.sentinel.window, "Elemento"))

    def test_returns_false_honestly_when_ocr_is_unavailable(self):
        """read_screen_words restituisce None quando nessun motore OCR e' disponibile per il
        profilo lingua dell'utente (core/vision/screen.py) - False onesto, mai un'eccezione."""
        adapter = mock.Mock()
        adapter.describe_element.return_value = _WINDOW_INFO
        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (200, 200))), \
             mock.patch("core.vision.screen.read_screen_words", return_value=None):
            self.assertFalse(word_visible_in_window(adapter, mock.sentinel.window, "Elemento"))

    def test_returns_false_honestly_when_the_window_is_not_readable(self):
        """describe_element restituisce None per un elemento non piu' ispezionabile (F3.2, stesso
        principio gia' seguito altrove) - False onesto invece di un'eccezione o un crop a caso."""
        adapter = mock.Mock()
        adapter.describe_element.return_value = None
        with mock.patch("core.vision.screen.read_screen_words") as read_words:
            self.assertFalse(word_visible_in_window(adapter, mock.sentinel.window, "Elemento"))
        read_words.assert_not_called()

    def test_only_the_window_bounds_are_captured_not_the_full_screen(self):
        """Scelta deliberata di privacy (vedi il docstring del modulo): il ritaglio passato
        all'OCR deve avere esattamente le dimensioni della finestra, mai l'intero schermo."""
        adapter = mock.Mock()
        adapter.describe_element.return_value = _WINDOW_INFO
        captured_sizes = []

        def _fake_read_words(image_path):
            with Image.open(image_path) as image:
                captured_sizes.append(image.size)
            return []

        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=Image.new("RGB", (1920, 1080))), \
             mock.patch("core.vision.screen.read_screen_words", side_effect=_fake_read_words):
            word_visible_in_window(adapter, mock.sentinel.window, "Elemento")

        self.assertEqual(captured_sizes, [(100, 50)], "il ritaglio OCR deve corrispondere ai bounds della finestra, non allo schermo intero")


if __name__ == "__main__":
    unittest.main()
