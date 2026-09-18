"""Test unitari per core/computer_use/fallback.py (F3.5.1/F3.5.2/F3.5.3, prima fetta della scala
di ripiego - mai iniziata prima d'ora). Il meccanismo generico (`try_strategies_in_order`) e'
testato con finti deterministici (nessuna dipendenza da UI Automation/dalla fixture - la logica
di ordinamento/registrazione/verifica e' pura, non ha bisogno di un ambiente Windows reale). Un
solo test end-to-end contro la fixture VERA dimostra il caso reale che ha motivato questo modulo
(F3.4: SelectionItem su un QListWidgetItem non ha un effetto vero), usando la coppia di strategie
gia' verificata NON "avvelenarsi" a vicenda - vedi il docstring del modulo per la coppia che
invece lo fa (UIA SelectionItem poi click pixel sullo STESSO elemento), deliberatamente non
usata qui perche' gia' provata inaffidabile."""
import time
import unittest

from core.computer_use.fallback import FallbackAttempt, FallbackOutcome, try_strategies_in_order


class TryStrategiesInOrderTests(unittest.TestCase):
    """Il meccanismo generico, con finti deterministici - nessuna dipendenza da UI Automation."""

    def test_the_first_strategy_that_verifies_wins_without_trying_the_rest(self):
        calls = []
        outcome = try_strategies_in_order(
            [
                ("first", lambda: calls.append("first")),
                ("second", lambda: calls.append("second")),
            ],
            verify=lambda: True,
        )

        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.successful_strategy, "first")
        self.assertEqual(calls, ["first"], "la seconda strategia non deve essere tentata se la prima verifica gia'")

    def test_falls_through_to_the_second_strategy_when_the_first_does_not_verify(self):
        verify_results = iter([False, True])
        outcome = try_strategies_in_order(
            [("first", lambda: None), ("second", lambda: None)],
            verify=lambda: next(verify_results),
        )

        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.successful_strategy, "second")
        self.assertEqual(len(outcome.attempts), 2)
        self.assertFalse(outcome.attempts[0].succeeded)
        self.assertTrue(outcome.attempts[1].succeeded)

    def test_an_exception_from_a_strategy_is_recorded_not_propagated(self):
        def _raising_strategy():
            raise RuntimeError("elemento non trovato")

        outcome = try_strategies_in_order(
            [("broken", _raising_strategy), ("working", lambda: None)],
            verify=lambda: True,
        )

        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.successful_strategy, "working")
        self.assertFalse(outcome.attempts[0].succeeded)
        self.assertIn("elemento non trovato", outcome.attempts[0].reason)

    def test_when_every_strategy_fails_the_outcome_is_unsuccessful_with_every_attempt_recorded(self):
        outcome = try_strategies_in_order(
            [("first", lambda: None), ("second", lambda: None)],
            verify=lambda: False,
        )

        self.assertFalse(outcome.succeeded)
        self.assertIsNone(outcome.successful_strategy)
        self.assertEqual(len(outcome.attempts), 2)
        self.assertTrue(all(not a.succeeded for a in outcome.attempts))

    def test_an_empty_strategy_list_fails_without_calling_verify(self):
        verify_calls = []
        outcome = try_strategies_in_order([], verify=lambda: verify_calls.append(True) or True)

        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.attempts, ())
        self.assertEqual(verify_calls, [])

    def test_a_strategy_marked_unsafe_after_failure_stops_the_ladder_instead_of_continuing(self):
        """F3.5.6: se la strategia marcata ESEGUE senza sollevare ma verify() non conferma, le
        strategie successive non devono nemmeno essere TENTATE - fermarsi con una diagnosi invece
        di incatenare alla cieca su un bersaglio potenzialmente gia' corrotto."""
        calls = []
        outcome = try_strategies_in_order(
            [
                ("risky", lambda: calls.append("risky"), True),
                ("never_reached", lambda: calls.append("never_reached")),
            ],
            verify=lambda: False,
        )

        self.assertFalse(outcome.succeeded)
        self.assertEqual(calls, ["risky"], "la seconda strategia non deve essere eseguita affatto")
        self.assertEqual(len(outcome.attempts), 1)
        self.assertIn("F3.5.6", outcome.attempts[0].reason)

    def test_a_strategy_not_marked_unsafe_still_falls_through_as_before(self):
        """Retrocompatibilita': una tupla a due elementi (senza il terzo flag) si comporta
        esattamente come prima di questo incremento - il default e' `False`, non interrompere."""
        outcome = try_strategies_in_order(
            [("first", lambda: None), ("second", lambda: None)],
            verify=lambda: False,
        )

        self.assertEqual(len(outcome.attempts), 2, "senza il marcatore, entrambe le strategie devono essere tentate")

    def test_an_unsafe_strategy_that_raises_does_not_stop_the_ladder(self):
        """Il marcatore riguarda solo il caso 'eseguita ma non verificata' - un'eccezione (la
        strategia non ha nemmeno agito) non lascia nulla da 'incatenare pericolosamente', quindi
        le successive vengono comunque tentate."""
        calls = []
        outcome = try_strategies_in_order(
            [
                ("risky_but_raises", (lambda: (_ for _ in ()).throw(RuntimeError("non trovato"))), True),
                ("reached", lambda: calls.append("reached")),
            ],
            verify=lambda: True,
        )

        self.assertTrue(outcome.succeeded)
        self.assertEqual(calls, ["reached"])

    def test_verify_is_called_after_the_action_not_before(self):
        """F3.5.3 ('ri-osservare PRIMA di cambiare strategia'): verify() deve vedere lo stato
        DOPO l'azione, mai prima - un ordine di chiamata sbagliato invaliderebbe l'intero scopo
        della verifica."""
        order = []
        try_strategies_in_order(
            [("only", lambda: order.append("action"))],
            verify=lambda: order.append("verify") or True,
        )

        self.assertEqual(order, ["action", "verify"])


class DataclassBehaviorTests(unittest.TestCase):
    def test_successful_strategy_is_none_when_nothing_succeeded(self):
        outcome = FallbackOutcome(succeeded=False, attempts=(FallbackAttempt("x", False, "motivo"),))

        self.assertIsNone(outcome.successful_strategy)

    def test_successful_strategy_finds_the_one_that_succeeded_even_if_not_last(self):
        outcome = FallbackOutcome(
            succeeded=True,
            attempts=(FallbackAttempt("a", False, "no"), FallbackAttempt("b", True), FallbackAttempt("c", False, "mai raggiunta")),
        )

        self.assertEqual(outcome.successful_strategy, "b")


class RealFixtureFallbackTests(unittest.TestCase):
    """Il caso reale che ha motivato questo modulo: F3.4 ha trovato che `ActionExecutor.select()`
    su un `QListWidgetItem` non ha un effetto vero. Qui la scala di ripiego prova PRIMA un
    selettore intenzionalmente sbagliato (simula "il selettore semantico non trova/non
    corrisponde piu' all'elemento" - un caso reale, non di comodo) e POI un click reale a
    coordinate pixel sull'elemento MAI toccato prima da un tentativo UIA - la coppia gia'
    verificata affidabile (vedi il docstring del modulo per quella NON affidabile, deliberatamente
    non usata qui)."""

    def setUp(self):
        import subprocess
        import sys
        from pathlib import Path

        from core.computer_agent import ComputerAgent
        from core.computer_use.executor import ActionExecutor
        from core.computer_use.selector import SelectorEngine
        from core.computer_use.ui_automation_adapter import UIAutomationAdapter

        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.addCleanup(self._terminate_process)
        self.adapter = UIAutomationAdapter()
        self.engine = SelectorEngine(self.adapter)
        self.executor = ActionExecutor()
        self.computer_agent = ComputerAgent()
        self.window = self.adapter.find_window_by_title("Jake Computer Use Fixture", timeout_seconds=15.0)

    def _terminate_process(self):
        import subprocess

        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def test_falling_back_from_a_bad_selector_to_a_real_click_selects_the_item(self):
        from core.computer_use.selector import ElementSelector

        input_field = self.engine.find_unique_element(
            self.window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_input"),
        )
        self.executor.set_value(input_field, "da selezionare")
        add_button = self.engine.find_unique_element(self.window, ElementSelector(name="Aggiungi", control_type="Button"))
        self.executor.invoke(add_button)
        time.sleep(0.3)

        item = self.engine.find_unique_element(self.window, ElementSelector(name="da selezionare", control_type="ListItem"))
        left, top, width, height = self.adapter.describe_element(item).bounds
        center_x, center_y = left + width // 2, top + height // 2

        def _bad_selector_strategy():
            # Simula un selettore che non corrisponde piu' (nome sbagliato) - una NoMatchError
            # vera, non simulata, esattamente come accadrebbe con un selettore reale ormai stale.
            self.engine.find_unique_element(self.window, ElementSelector(name="questo nome e' sbagliato"))

        def _verify_selected():
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                remove_button = self.engine.find_unique_element(
                    self.window, ElementSelector(name="Rimuovi selezionato", control_type="Button"),
                )
                if self.adapter.describe_element(remove_button).enabled:
                    return True
                time.sleep(0.1)
            return False

        outcome = try_strategies_in_order(
            [
                ("bad_selector", _bad_selector_strategy),
                ("pixel_click", lambda: self.computer_agent.click_point(center_x, center_y)),
            ],
            verify=_verify_selected,
        )

        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.successful_strategy, "pixel_click")
        self.assertFalse(outcome.attempts[0].succeeded)

    def test_marking_uia_select_unsafe_stops_the_ladder_instead_of_reproducing_the_poisoning_bug(self):
        """F3.5.6, dimostrato contro il buco REALE (non un finto): incatenare `select()` via UIA
        poi un click pixel sullo STESSO `QListWidgetItem` (a differenza della coppia "selettore
        sbagliato poi click pixel" del test sopra, che NON si avvelena) e' esattamente la coppia
        che il docstring del modulo documenta come inaffidabile - il click pixel, di per se'
        affidabile, smette di funzionare se preceduto da questo tentativo UIA fallito. Marcando
        'uia_select' come `unsafe_after_failure`, la scala si ferma DOPO il primo tentativo invece
        di eseguire comunque il click pixel e scoprire solo alla fine che non ha funzionato -
        `pixel_click` non deve nemmeno essere chiamato."""
        from core.computer_use.selector import ElementSelector

        input_field = self.engine.find_unique_element(
            self.window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_input"),
        )
        self.executor.set_value(input_field, "da non toccare")
        add_button = self.engine.find_unique_element(self.window, ElementSelector(name="Aggiungi", control_type="Button"))
        self.executor.invoke(add_button)
        time.sleep(0.3)

        item = self.engine.find_unique_element(self.window, ElementSelector(name="da non toccare", control_type="ListItem"))
        left, top, width, height = self.adapter.describe_element(item).bounds
        center_x, center_y = left + width // 2, top + height // 2
        pixel_click_calls = []

        def _pixel_click():
            pixel_click_calls.append(True)
            self.computer_agent.click_point(center_x, center_y)

        def _verify_selected():
            remove_button = self.engine.find_unique_element(
                self.window, ElementSelector(name="Rimuovi selezionato", control_type="Button"),
            )
            return self.adapter.describe_element(remove_button).enabled

        outcome = try_strategies_in_order(
            [
                ("uia_select", lambda: self.executor.select(item), True),
                ("pixel_click", _pixel_click),
            ],
            verify=_verify_selected,
        )

        self.assertFalse(outcome.succeeded)
        self.assertEqual(pixel_click_calls, [], "pixel_click non deve essere tentato dopo uno stop di sicurezza")
        self.assertEqual(len(outcome.attempts), 1)
        self.assertIn("F3.5.6", outcome.attempts[0].reason)


if __name__ == "__main__":
    unittest.main()
