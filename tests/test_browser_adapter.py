"""Test per core/computer_use/browser_adapter.py (F3.6.1/F3.6.2, prima fetta di F3.6 - mai
iniziata prima d'ora). Lancia DAVVERO Edge contro benchmarks/browser_fixture.html in un processo
separato, con un profilo ISOLATO (mai il profilo reale dell'utente - vedi il docstring del modulo
per il rischio di privacy gia' trovato e la ragione dei flag di isolamento). Saltato
esplicitamente se Edge non e' installato in questo ambiente (`BrowserNotFoundError`), non fatto
fallire - lo stesso principio gia' seguito per l'OCR (F3.5.1, `core/vision/screen.py::
ocr_available`)."""
import time
import unittest
from pathlib import Path

from core.computer_agent import ComputerAgent
from core.computer_use.browser_adapter import (
    BrowserNotFoundError,
    find_edge_executable,
    find_page_document,
    is_password_field,
    launch_isolated_browser,
    read_address_bar_text,
    read_page_text,
    find_isolated_browser_window,
)
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter

_FIXTURE_URL = f"file:///{(Path(__file__).resolve().parent.parent / 'benchmarks' / 'browser_fixture.html').as_posix()}"


def _edge_available() -> bool:
    try:
        find_edge_executable()
        return True
    except BrowserNotFoundError:
        return False


@unittest.skipUnless(_edge_available(), "Microsoft Edge non e' installato in questo ambiente")
class RealBrowserFixtureTests(unittest.TestCase):
    def setUp(self):
        self.browser = launch_isolated_browser(_FIXTURE_URL)
        self.addCleanup(self.browser.terminate_and_cleanup)
        self.adapter = UIAutomationAdapter()
        self.window = find_isolated_browser_window(self.adapter, self.browser, timeout_seconds=25.0)

    def test_the_page_content_is_not_visible_before_waking_the_accessibility_tree(self):
        """Buco reale trovato investigando, non ipotizzato (vedi il docstring del modulo): prima
        di una ricerca dedicata, il contenuto della pagina - anche gia' completamente caricato -
        non e' presente nell'albero UI Automation, solo il chrome del browser lo e'."""
        matches = self.adapter.find_matching_elements(self.window, control_type="Document")
        self.assertEqual(matches, [], "il nodo Document non deve essere gia' presente prima della prima sveglia")

    def test_find_page_document_wakes_the_tree_and_returns_the_page_root(self):
        document = find_page_document(self.adapter, self.window)

        info = self.adapter.describe_element(document)
        self.assertEqual(info.control_type, "Document")

    def test_the_real_page_elements_are_found_inside_the_document(self):
        document = find_page_document(self.adapter, self.window)
        engine = SelectorEngine(self.adapter)

        button = engine.wait_for_unique_element(document, ElementSelector(name="Aggiungi", control_type="Button"))
        input_field = engine.wait_for_unique_element(document, ElementSelector(name="Campo di testo", control_type="Edit"))
        link = engine.wait_for_unique_element(document, ElementSelector(name="Un link di prova", control_type="Hyperlink"))

        self.assertEqual(button.CurrentName, "Aggiungi")
        self.assertEqual(input_field.CurrentName, "Campo di testo")
        self.assertEqual(link.CurrentName, "Un link di prova")

    def test_browser_chrome_elements_are_not_inside_the_document(self):
        """F3.6.2 ("distinguere contenuto pagina e browser chrome"): un bottone del CHROME del
        browser (es. "Nuova scheda") non deve comparire cercando SOLO dentro il `Document` - la
        separazione e' strutturale (il confine del nodo `Document`), non un elenco di nomi
        "chrome" da escludere a mano."""
        document = find_page_document(self.adapter, self.window)

        matches = self.adapter.find_matching_elements(document, name="Nuova scheda")
        self.assertEqual(matches, [])

    def test_computer_agent_click_and_type_work_against_the_document_root(self):
        """F3.4.2 + F3.6 insieme: `ComputerAgent.type_into_element`/`click_element` con `root`
        (il nodo `Document`, non `window_title` - un browser non ne ha uno prevedibile) invece di
        assemblare adapter/selector/executor a mano - lo stesso genere di dimostrazione end-to-end
        gia' fatta per la fixture Qt in `tests/test_computer_use_integration.py`, qui contro un
        browser vero."""
        document = find_page_document(self.adapter, self.window)
        agent = ComputerAgent()

        type_result = agent.type_into_element("dal browser", root=document, name="Campo di testo", control_type="Edit")
        self.assertTrue(type_result.success)
        click_result = agent.click_element(root=document, name="Aggiungi", control_type="Button")
        self.assertTrue(click_result.success)

        # Il gestore onclick della fixture scrive il valore del campo nel paragrafo di output -
        # rileggere il Document (potrebbe essere cambiato) e cercare quel testo e' la prova
        # indipendente che l'intera catena ha avuto un effetto reale, non solo che nessuna delle
        # due chiamate abbia sollevato.
        document_after = find_page_document(self.adapter, self.window)
        matches = self.adapter.find_matching_elements(document_after, name="dal browser")
        self.assertEqual(len(matches), 1, "il paragrafo di output deve mostrare davvero il testo digitato")

    def test_read_address_bar_text_shows_a_normalized_path_not_the_exact_url(self):
        """F3.6.5 (prima fetta - "verificare URL"): buco reale trovato leggendo davvero la barra
        degli indirizzi, non ipotizzato - per un `file:///` locale Edge la mostra NORMALIZZATA
        (percorso Windows con `/`, senza lo schema `file:///` davanti), diversa carattere per
        carattere dall'URL passato a `launch_isolated_browser`. Verificato che il pezzo
        DISTINTIVO (il nome del file) sia comunque presente - la sottostringa e' il confronto
        onesto, mai l'uguaglianza esatta con questo genere di URL."""
        text = read_address_bar_text(self.adapter, self.window)

        self.assertIsNotNone(text)
        self.assertIn("browser_fixture.html", text)
        self.assertNotEqual(text, _FIXTURE_URL, "la barra normalizza il file:// - non deve mai coincidere per uguaglianza esatta")

    def test_is_password_field_distinguishes_a_real_password_field_from_a_normal_one(self):
        """F3.6.7 (prima fetta - "redigere password e campi sensibili"): segnale STRUTTURALE
        (`CurrentIsPassword`), non un'euristica sul nome del campo - verificato il contrasto tra
        i due campi della fixture, non assunto da uno solo."""
        document = find_page_document(self.adapter, self.window)
        engine = SelectorEngine(self.adapter)

        password_field = engine.wait_for_unique_element(document, ElementSelector(name="Password", control_type="Edit"))
        normal_field = engine.wait_for_unique_element(document, ElementSelector(name="Campo di testo", control_type="Edit"))

        self.assertTrue(is_password_field(password_field))
        self.assertFalse(is_password_field(normal_field))

    def test_a_real_password_fields_value_is_already_masked_by_chromium_not_by_this_module(self):
        """Rassicurazione reale, non assunta: il campo password della fixture ha un valore VERO
        ("segreto123", vedi benchmarks/browser_fixture.html) - se questo test leggesse quel
        valore in chiaro, sarebbe un buco di sicurezza reale. Chromium lo maschera GIA' a livello
        di UI Automation, prima che questo modulo debba fare qualunque cosa."""
        import comtypes
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA

        document = find_page_document(self.adapter, self.window)
        engine = SelectorEngine(self.adapter)
        password_field = engine.wait_for_unique_element(document, ElementSelector(name="Password", control_type="Edit"))

        value_pattern = password_field.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)

        self.assertNotEqual(value_pattern.CurrentValue, "segreto123", "il valore vero non deve mai essere leggibile via UI Automation")

    def test_clicking_a_real_link_navigates_to_a_second_local_page(self):
        """F3.6.3 (prima fetta - "supportare navigazione"): nessun codice nuovo necessario - la
        composizione gia' esistente di `click_element` (F3.4.2/F3.6, Invoke su un `Hyperlink`) +
        `read_address_bar_text` (F3.6.5) + `find_page_document` (F3.6.1) basta gia' a dimostrare
        una navigazione VERA (non un'ancora "#" sulla stessa pagina - il link della fixture punta
        a `browser_fixture_page2.html`, una seconda pagina locale reale)."""
        document = find_page_document(self.adapter, self.window)
        agent = ComputerAgent()

        click_result = agent.click_element(root=document, name="Un link di prova", control_type="Hyperlink")
        self.assertTrue(click_result.success)

        address_after = read_address_bar_text(self.adapter, self.window)
        self.assertIn("browser_fixture_page2.html", address_after)

        new_document = find_page_document(self.adapter, self.window)
        info = self.adapter.describe_element(new_document)
        self.assertEqual(info.name, "Jake Browser Fixture - Pagina 2", "la pagina caricata deve essere davvero la seconda, non la stessa")

    def test_read_page_text_collects_visible_text_without_ever_exposing_the_password_value(self):
        """F3.6.1 (resto - leggere il testo visibile, non solo trovare un elemento per nome).
        Verifica sia il caso positivo (il testo vero c'e') sia quello di sicurezza (il valore
        vero del campo password, "segreto123", non compare MAI - solo la sua etichetta)."""
        document = find_page_document(self.adapter, self.window)

        text = read_page_text(self.adapter, document)

        self.assertIn("Jake Browser Fixture", text)
        self.assertIn("Aggiungi", text)
        self.assertIn("Un link di prova", text)
        self.assertIn("Password", text, "l'ETICHETTA del campo password e' testo pubblico, deve comparire")
        self.assertNotIn("segreto123", text, "il VALORE vero del campo password non deve mai comparire nel testo estratto")

    def test_copying_from_a_real_password_field_never_reaches_the_clipboard(self):
        """F3.6.7 (resto - "redigere... via clipboard", CHIUDE la meta' clipboard): verificato
        DAVVERO con un click+Ctrl+A+Ctrl+C reali su un campo password vero, non assunto dalla
        documentazione - un controllo POSITIVO sullo stesso meccanismo contro il campo NORMALE
        (che DEVE funzionare) prova che il fallimento sul campo password non e' un bug del test
        stesso (mouse/focus che non arrivano affatto), lo stesso principio "controllo positivo
        indipendente" gia' seguito da `tests/test_ui_automation_adapter.py::
        ScopeLimitedToTargetWindowTests`.

        **Muta la clipboard REALE del sistema, deliberatamente** (nessun altro modo onesto di
        verificare questo - la clipboard e' uno stato globale del desktop, non isolato per
        processo come la finestra del browser) - il contenuto originale e' salvato e RIPRISTINATO
        in un blocco `finally`, stesso principio "chi muta uno stato condiviso lo ripristina" gia'
        seguito per i processi/profili lanciati in questa sessione."""
        import time

        import pyautogui
        import pyperclip
        import win32api
        import win32con
        import win32gui

        document = find_page_document(self.adapter, self.window)
        engine = SelectorEngine(self.adapter)

        def _select_all_and_copy(automation_id: str, marker: str) -> str:
            element = engine.find_unique_element(document, ElementSelector(automation_id=automation_id))
            rect = element.CurrentBoundingRectangle
            win32gui.SetForegroundWindow(self.window.CurrentNativeWindowHandle)
            time.sleep(0.3)
            win32api.SetCursorPos(((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.3)
            pyperclip.copy(marker)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.3)
            return pyperclip.paste()

        original_clipboard = pyperclip.paste()
        try:
            self.agent = ComputerAgent()
            self.agent.type_into_element("testo copiabile", root=document, automation_id="fixture-input")

            control_result = _select_all_and_copy("fixture-input", "MARCATORE_CONTROLLO")
            self.assertEqual(control_result, "testo copiabile", "il controllo positivo deve dimostrare che click+Ctrl+A+Ctrl+C funzionano davvero")

            password_result = _select_all_and_copy("fixture-password", "MARCATORE_PASSWORD")
            self.assertEqual(
                password_result, "MARCATORE_PASSWORD",
                "Ctrl+C su un campo password non deve MAI cambiare la clipboard - Chromium blocca la copia, non solo maschera il valore",
            )
            self.assertNotIn("segreto123", password_result)
        finally:
            pyperclip.copy(original_clipboard)

    def test_ocr_of_a_real_screenshot_never_exposes_the_password_value(self):
        """F3.6.7 (resto - "redigere... via OCR", CHIUDE F3.6.7 per intero): verificato con uno
        screenshot REALE dello schermo intero (mai un ritaglio piccolo - buco reale trovato
        investigando: l'API OCR di Windows usata da `core/vision/screen.py::read_screen_text` non
        restituisce nulla su un ritaglio di poche decine di pixel, un limite dell'API stessa non
        di questo modulo - verificato con un probe dedicato, non ipotizzato) - un controllo
        POSITIVO (il testo del campo NORMALE, digitato apposta, DEVE comparire nell'OCR) prova che
        l'OCR sta leggendo davvero lo schermo, non fallendo silenziosamente per un altro motivo."""
        import tempfile

        from core.vision.screen import capture_screenshot_image, read_screen_text

        if not self._ocr_available():
            self.skipTest("nessun motore OCR disponibile in questo ambiente")

        document = find_page_document(self.adapter, self.window)
        self.agent = ComputerAgent()
        self.agent.type_into_element("TESTOCONTROLLOOCR", root=document, automation_id="fixture-input")

        image = capture_screenshot_image()
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "screen.png"
            image.save(path)
            text = read_screen_text(path)

        self.assertIsNotNone(text)
        self.assertIn("TESTOCONTROLLOOCR", text, "il controllo positivo deve dimostrare che l'OCR sta leggendo davvero lo schermo")
        self.assertNotIn("segreto123", text, "il valore vero del campo password non deve mai comparire nel testo OCR")

    @staticmethod
    def _ocr_available() -> bool:
        from core.vision.screen import ocr_available
        return ocr_available()

    def test_uploading_a_real_local_file_through_the_native_open_dialog(self):
        """F3.6.3 (resto - "upload", CHIUDE per intero - correzione di un'indagine precedente,
        vedi ROADMAP_EXECUTION.md): un `<input type="file">` reale, guidato attraverso il dialogo
        NATIVO "Apri" di Windows dall'inizio alla fine - click sul campo, digitare il percorso,
        cliccare "Apri" - verificato che il file scelto arrivi DAVVERO alla pagina (il nome
        compare nell'elemento osservabile), non solo che il dialogo si sia aperto e chiuso.

        Reso possibile da `UIAutomationAdapter.wait_for_new_win32_window`/`element_from_handle`
        (F3.1.2 Task 13, adozione) - il dialogo NON compare nell'enumerazione dei figli del
        desktop secondo UI Automation (lo stesso buco gia' noto), ma la nuova coppia di metodi lo
        risolve passando dall'enumerazione WIN32. Gli automation_id usati per l'edit del nome file
        (`1148`) e il bottone "Apri" (`1`) sono una convenzione NUMERICA stabile del dialogo
        comune di Windows (indipendente dalla lingua, a differenza di un nome localizzato come
        "Apri") - verificati empiricamente con un probe dedicato PRIMA di scrivere questo test,
        non presi dalla documentazione.

        **Buco reale trovato in CI, non ipotizzato**: l'automation_id `1` e' condiviso da una
        riga della lista file (control_type `ListItem`) - una prima versione disambiguava con
        `control_type="SplitButton"` (il control_type osservato in locale), ma su un runner CI
        con un build/tema diverso di Explorer il bottone "Apri" si e' rivelato un `Button`
        semplice, non uno `SplitButton` - lo stesso ID, un control_type diverso a seconda
        dell'ambiente. Corretto cercando SOLO per automation_id, poi scartando programmaticamente
        il candidato `ListItem` (l'unico control_type che il bottone "Apri" non potra' MAI avere),
        invece di indovinare un control_type specifico."""
        import tempfile

        document = find_page_document(self.adapter, self.window)
        agent = ComputerAgent()

        with tempfile.TemporaryDirectory() as tmp_dir:
            target_file = Path(tmp_dir) / "jake_upload_probe.txt"
            target_file.write_text("contenuto di prova", encoding="utf-8")

            baseline = self.adapter.snapshot_win32_top_level_window_handles()
            click_result = agent.click_element(root=document, automation_id="fixture-file-input")
            self.assertTrue(click_result.success, click_result)

            dialog = self.adapter.wait_for_new_win32_window(baseline, timeout_seconds=10.0)
            engine = SelectorEngine(self.adapter)
            filename_edit = engine.wait_for_unique_element(dialog, ElementSelector(automation_id="1148", control_type="Edit"), timeout_seconds=5.0)

            candidates = engine.find_all(dialog, ElementSelector(automation_id="1"))
            not_a_list_row = [c for c in candidates if c.control_type != "ListItem"]
            self.assertEqual(len(not_a_list_row), 1, f"atteso un solo candidato non-ListItem per automation_id='1': {candidates}")
            open_button = engine.find_unique_element(dialog, ElementSelector(automation_id="1", control_type=not_a_list_row[0].control_type))

            from core.computer_use.executor import ActionExecutor
            executor = ActionExecutor()
            executor.set_value(filename_edit, str(target_file.resolve()))
            executor.invoke(open_button)

            matches = engine.wait_for_unique_element(document, ElementSelector(name="jake_upload_probe.txt"), timeout_seconds=5.0)
            self.assertEqual(matches.CurrentName, "jake_upload_probe.txt")


if __name__ == "__main__":
    unittest.main()


class IsolatedProfileCleanupTests(unittest.TestCase):
    """Bug trovato dal gate delle app reali: i processi figli di Edge tenevano bloccato il profilo
    temporaneo dopo la chiusura del processo principale e rmtree(ignore_errors) falliva in
    silenzio (230 profili jake_edge_* trovati in %TEMP%). Qui un processo "figlio" vero tiene un
    file aperto nel profilo: la pulizia deve attenderlo/terminarlo e cancellare davvero la cartella."""

    def test_a_process_still_holding_the_profile_is_stopped_and_the_folder_removed(self):
        import subprocess
        import sys
        import tempfile

        from core.computer_use.browser_adapter import IsolatedBrowserProcess

        profile = tempfile.mkdtemp(prefix="jake_edge_test_")
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import sys, time; f = open(sys.argv[1] + '/lock', 'w'); time.sleep(60)", profile])
        self.addCleanup(lambda: holder.kill() if holder.poll() is None else None)
        main = subprocess.Popen([sys.executable, "-c", "pass"])
        for _ in range(50):  # il file bloccato deve esistere prima della pulizia
            if Path(profile, "lock").exists():
                break
            time.sleep(0.1)

        IsolatedBrowserProcess(main, profile).terminate_and_cleanup(timeout_seconds=1.0)

        self.assertFalse(Path(profile).exists())
        self.assertIsNotNone(holder.poll(), "il processo che teneva il profilo non deve restare vivo")


class FindIsolatedBrowserWindowTests(unittest.TestCase):
    """Verificato il 25/09/2026: il processo Edge lanciato puo' uscire (codice 0) e lasciare la
    finestra a un altro processo dello STESSO profilo temporaneo; cercare per il PID del
    lanciatore falliva sempre (e rompeva anche la skill read_web_page)."""

    def test_the_window_of_a_process_sharing_the_isolated_profile_is_found(self):
        from unittest import mock

        from core.computer_use.browser_adapter import IsolatedBrowserProcess, find_isolated_browser_window
        from core.computer_use.ui_automation_adapter import WindowNotFoundError

        browser = IsolatedBrowserProcess(mock.MagicMock(pid=100), "C:/tmp/jake_edge_x")
        adapter = mock.MagicMock()
        adapter.find_window_by_process_id.side_effect = lambda pid, timeout_seconds: (
            "finestra" if pid == 200 else (_ for _ in ()).throw(WindowNotFoundError(str(pid))))
        with mock.patch.object(browser, "_profile_processes", return_value=[mock.MagicMock(pid=200)]):
            self.assertEqual(find_isolated_browser_window(adapter, browser, timeout_seconds=1.0), "finestra")
        with mock.patch.object(browser, "_profile_processes", return_value=[]), self.assertRaises(WindowNotFoundError):
            find_isolated_browser_window(adapter, browser, timeout_seconds=0.3)

