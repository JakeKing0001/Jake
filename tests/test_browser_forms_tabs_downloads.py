"""F3.6.3 (resto) e F3.6.6 contro Edge VERO isolato (profilo temporaneo, pagine locali, nessuna rete):
modulo compilato e inviato dopo la policy, invio bloccato dalla policy senza alcun effetto, apertura e
cambio di scheda, download nella cartella temporanea dell'istanza (mai nella cartella Download
dell'utente) dopo la policy, e stop davanti a una verifica umana (CAPTCHA) senza toccarla."""
import os
import time
import unittest
from pathlib import Path

from core.computer_agent import ComputerAgent
from core.computer_use.browser_adapter import (
    BrowserNotFoundError,
    find_edge_executable,
    find_page_document,
    launch_isolated_browser,
    list_tabs,
    read_page_text,
    select_tab,
    wait_for_download,
)
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter
from core.policy_engine import PolicyEngine

# Mouse e tastiera veri: mai input fuori dal browser isolato (vedi tests/fixture_input_guard.py).
from tests.fixture_input_guard import install as setUpModule, uninstall as tearDownModule  # noqa: E402,F401

_ROOT = Path(__file__).resolve().parent.parent


def _edge_available() -> bool:
    try:
        find_edge_executable()
        return True
    except BrowserNotFoundError:
        return False


@unittest.skipUnless(_edge_available(), "Microsoft Edge non e' installato in questo ambiente")
class _BrowserCase(unittest.TestCase):
    PAGE = "browser_fixture_forms.html"

    def setUp(self):
        url = (_ROOT / "benchmarks" / self.PAGE).as_uri()
        self.browser = launch_isolated_browser(url)
        self.addCleanup(self.browser.terminate_and_cleanup)
        self.adapter = UIAutomationAdapter()
        self.window = self.adapter.find_window_by_process_id(self.browser.process.pid, timeout_seconds=25.0)
        self.document = find_page_document(self.adapter, self.window, timeout_seconds=15.0)
        self.agent = ComputerAgent()

    def _page_text(self):
        return read_page_text(self.adapter, find_page_document(self.adapter, self.window, timeout_seconds=5.0))

    def _wait_text(self, fragment, timeout=5.0):
        deadline = time.monotonic() + timeout
        text = ""
        while time.monotonic() < deadline:
            text = self._page_text()
            if fragment in text:
                return text
            time.sleep(0.3)
        return text


class FormTests(_BrowserCase):
    def _fill(self):
        self.assertTrue(self.agent.type_into_element("Mario", root=self.document, name="Nome", control_type="Edit").success)
        self.assertTrue(self.agent.type_into_element("Roma", root=self.document, name="Citta", control_type="Edit").success)
        tick = self.agent.click_element(root=self.document, name="Accetto le condizioni", control_type="CheckBox", idempotent=False)
        self.assertTrue(tick.success, tick)

    def test_a_form_is_filled_and_submitted_after_the_policy(self):
        self._fill()
        self.agent.policy_engine = PolicyEngine()
        sent = self.agent.click_element(root=self.document, name="Invia modulo", control_type="Button", risk_intent="UI_SUBMIT")
        self.assertTrue(sent.success, sent)
        self.assertIn("Inviato: Mario / Roma / accetto=true", self._wait_text("Inviato:"))

    def test_a_submit_blocked_by_policy_never_reaches_the_page(self):
        self._fill()
        self.agent.policy_engine = PolicyEngine(blocked_intents={"UI_SUBMIT"})
        sent = self.agent.click_element(root=self.document, name="Invia modulo", control_type="Button", risk_intent="UI_SUBMIT")
        self.assertEqual(sent.error, "POLICY_BLOCKED")
        time.sleep(0.5)
        self.assertNotIn("Inviato:", self._page_text())


class TabTests(_BrowserCase):
    def test_a_link_opens_a_new_tab_and_jake_switches_between_them(self):
        opened = self.agent.click_element(root=self.document, name="Apri in una nuova scheda", control_type="Hyperlink")
        self.assertTrue(opened.success, opened)
        deadline = time.monotonic() + 10
        tabs = []
        while time.monotonic() < deadline and len(tabs) < 2:
            tabs = list_tabs(self.adapter, self.window)
            time.sleep(0.3)
        self.assertEqual(len(tabs), 2, tabs)
        self.assertTrue(select_tab(self.adapter, self.window, "Moduli"))
        self.assertIn("Jake Browser Moduli", self._wait_text("Jake Browser Moduli"))
        other = next(t for t in tabs if "Moduli" not in t)
        self.assertTrue(select_tab(self.adapter, self.window, other))
        self.assertFalse(select_tab(self.adapter, self.window, "Jake Browser"), "due schede corrispondono: nessuna scelta a caso")


class DownloadTests(_BrowserCase):
    def test_a_download_lands_in_the_isolated_folder_never_in_the_user_downloads(self):
        user_downloads = Path.home() / "Downloads"
        target = "jake_download_fixture.txt"
        existed_before = (user_downloads / target).exists()
        self.agent.policy_engine = PolicyEngine()
        clicked = self.agent.click_element(root=self.document, name="Scarica il file di prova", control_type="Hyperlink",
                                           risk_intent="UI_DOWNLOAD")
        self.assertTrue(clicked.success, clicked)
        downloaded = wait_for_download(self.browser.download_dir, target)
        self.assertIsNotNone(downloaded)
        self.assertEqual(downloaded.read_text(encoding="utf-8"), "Jake download fixture")
        self.assertEqual((user_downloads / target).exists(), existed_before, "la cartella personale non va mai toccata")

    def test_a_blocked_download_never_starts(self):
        self.agent.policy_engine = PolicyEngine(blocked_intents={"UI_DOWNLOAD"})
        clicked = self.agent.click_element(root=self.document, name="Scarica il file di prova", control_type="Hyperlink",
                                           risk_intent="UI_DOWNLOAD")
        self.assertEqual(clicked.error, "POLICY_BLOCKED")
        time.sleep(1.0)
        self.assertEqual(os.listdir(self.browser.download_dir), [])


class HumanVerificationTests(_BrowserCase):
    PAGE = "browser_fixture_captcha.html"

    def test_jake_stops_at_a_captcha_and_never_touches_it(self):
        result = self.agent.click_element(root=self.document, name="Continua", control_type="Button")
        self.assertEqual(result.error, "HUMAN_VERIFICATION_REQUIRED")
        typed = self.agent.click_element(root=self.document, name="Non sono un robot", control_type="CheckBox")
        self.assertEqual(typed.error, "HUMAN_VERIFICATION_REQUIRED")
        box = SelectorEngine(self.adapter).wait_for_unique_element(
            self.document, ElementSelector(name="Non sono un robot", control_type="CheckBox"), timeout_seconds=5.0,
        )
        self.assertEqual(self.adapter.describe_element(box).toggle_state, "off")


if __name__ == "__main__":
    unittest.main()
