"""Test di integrazione per l'intera catena Computer Use Engine 3.0 costruita in questa sessione
(F3.1 fixture -> F3.2 adapter -> F3.3 selector -> F3.4 executor -> F3.5 fallback), non un test
unitario di un singolo modulo. Dimostra Task 2/10 di F3.1.2 ("rimuovi con conferma") completato
per DAVVERO end-to-end - la prima volta in questo intero filone di lavoro che questo task
specifico funziona, combinando: un selettore che trova gli elementi per nome (F3.3), una scala di
ripiego che passa da UI Automation a un click reale quando il primo non ha un effetto vero (F3.5,
il buco di SelectionItem su QListWidgetItem trovato in F3.4), un'attesa a polling per il dialogo
modale invece di uno sleep fisso (F3.4.7, adozione via `SelectorEngine.wait_for_unique_element`),
e l'executor per invocare i bottoni (F3.4). ZERO `time.sleep()` fissi in tutto il flusso - solo
attese con timeout che si fermano appena la condizione e' vera.

`ChangeTabAndToggleEndToEndTests` completa Task 4/10 ("cambia tab e spunta l'opzione") - a
differenza di Task 2, qui non serve alcuna scala di ripiego: sia `select()` su un `TabItem`
(F3.4, verificato affidabile con la prova indipendente del checkbox raggiungibile) sia `toggle()`
(F3.4.1) funzionano gia' in modo affidabile via UI Automation pura - questo test li combina in
un unico flusso end-to-end DAVVERO guidato dall'esterno, invece di restare due fatti verificati
separatamente in `tests/test_executor.py`."""
import subprocess
import sys
import time
import unittest
from pathlib import Path

from core.computer_agent import ComputerAgent
from core.computer_use.executor import ActionExecutor
from core.computer_use.fallback import try_strategies_in_order
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_WINDOW_TITLE = "Jake Computer Use Fixture"


class RemoveWithConfirmationEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        self.addCleanup(self._terminate_process)
        self.adapter = UIAutomationAdapter()
        self.engine = SelectorEngine(self.adapter)
        self.executor = ActionExecutor()
        self.computer_agent = ComputerAgent()
        self.window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)

    def _terminate_process(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def _remove_button_is_enabled(self) -> bool:
        remove_button = self.engine.wait_for_unique_element(
            self.window, ElementSelector(name="Rimuovi selezionato", control_type="Button"), timeout_seconds=2.0,
        )
        return self.adapter.describe_element(remove_button).enabled

    def test_add_select_remove_and_confirm_completes_for_real(self):
        # Task 1/10: digita e clicca Aggiungi (Invoke/Value, gia' verificati affidabili in F3.4).
        input_field = self.engine.wait_for_unique_element(
            self.window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_input"),
        )
        self.executor.set_value(input_field, "elemento di prova")
        add_button = self.engine.wait_for_unique_element(self.window, ElementSelector(name="Aggiungi", control_type="Button"))
        self.executor.invoke(add_button)
        item = self.engine.wait_for_unique_element(
            self.window, ElementSelector(name="elemento di prova", control_type="ListItem"), timeout_seconds=3.0,
        )

        # Selezionare l'elemento: SelectionItem via UIA non ha un effetto vero su un
        # QListWidgetItem (F3.4, buco reale trovato in questa sessione). La scala di ripiego
        # (F3.5) qui usa deliberatamente UN SOLO gradino (click reale a coordinate pixel), non
        # "prova prima UIA poi il click" - F3.5 ha gia' trovato (vedi il suo docstring) che
        # incatenare un tentativo UIA fallito PRIMA di un click reale sullo STESSO elemento
        # "avvelena" lo stato e fa fallire anche il click che da solo funzionerebbe. Il selettore
        # sbagliato di `tests/test_fallback.py` dimostra il meccanismo generico con una coppia
        # che NON si avvelena; qui invece si dimostra il flusso reale end-to-end con la strategia
        # gia' nota per funzionare, senza reintrodurre il buco per amore di "usare la scala".
        bounds = self.adapter.describe_element(item).bounds
        left, top, width, height = bounds
        center_x, center_y = left + width // 2, top + height // 2
        selection_outcome = try_strategies_in_order(
            [("pixel_click", lambda: self.computer_agent.click_point(center_x, center_y))],
            verify=self._remove_button_is_enabled,
        )
        self.assertTrue(selection_outcome.succeeded, selection_outcome.attempts)

        # Invocare "Rimuovi selezionato" apre un dialogo MODALE - trovato con un'attesa a
        # polling (F3.4.7), non uno sleep fisso: il dialogo e' un DISCENDENTE della finestra
        # fixture nell'albero UI Automation (verificato in questa sessione), non un figlio del
        # desktop, quindi cercato a partire da `self.window`.
        remove_button = self.engine.wait_for_unique_element(
            self.window, ElementSelector(name="Rimuovi selezionato", control_type="Button"),
        )
        self.executor.invoke(remove_button)
        dialog = self.engine.wait_for_unique_element(
            self.window, ElementSelector(name="Conferma", control_type="Window"), timeout_seconds=3.0,
        )
        yes_button = self.engine.wait_for_unique_element(dialog, ElementSelector(name="Sì", control_type="Button"))
        self.executor.invoke(yes_button)

        # Verifica finale: l'elemento e' davvero sparito - un'attesa a polling, non uno sleep
        # fisso, per lo stesso motivo di ogni altro passo sopra.
        deadline = time.monotonic() + 3.0
        remaining = self.engine.find_all(self.window, ElementSelector(name="elemento di prova"))
        while remaining and time.monotonic() < deadline:
            time.sleep(0.05)
            remaining = self.engine.find_all(self.window, ElementSelector(name="elemento di prova"))
        self.assertEqual(remaining, [], "l'elemento deve essere davvero rimosso, non solo il dialogo chiuso")


class ChangeTabAndToggleEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        self.addCleanup(self._terminate_process)
        self.adapter = UIAutomationAdapter()
        self.engine = SelectorEngine(self.adapter)
        self.executor = ActionExecutor()
        self.window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)

    def _terminate_process(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def test_switching_tab_then_toggling_the_option_completes_for_real(self):
        # Cambiare tab: SelectionItem su un TabItem funziona davvero (F3.4, a differenza del
        # QListWidgetItem di Task 2) - nessuna scala di ripiego necessaria qui.
        tab_two = self.engine.wait_for_unique_element(self.window, ElementSelector(name="Tab 2", control_type="TabItem"))
        self.executor.select(tab_two)

        tab_one_after = self.engine.wait_for_unique_element(self.window, ElementSelector(name="Tab 1", control_type="TabItem"))
        tab_two_after = self.engine.wait_for_unique_element(self.window, ElementSelector(name="Tab 2", control_type="TabItem"))
        self.assertFalse(self.adapter.describe_element(tab_one_after).selected)
        self.assertTrue(self.adapter.describe_element(tab_two_after).selected)

        # Il checkbox "Opzione" vive nella tab 2 - raggiungibile via UI Automation solo perche' la
        # tab e' DAVVERO cambiata a livello Qt (la stessa prova indipendente gia' usata in F3.4:
        # se select() avesse solo "riportato" successo senza un effetto reale, come per
        # SelectionItem su un QListWidgetItem, questo elemento non sarebbe qui a essere trovato).
        checkbox = self.engine.wait_for_unique_element(self.window, ElementSelector(name="Opzione", control_type="CheckBox"))
        self.assertEqual(self.adapter.describe_element(checkbox).toggle_state, "off")

        self.executor.toggle(checkbox)

        checkbox_after = self.engine.wait_for_unique_element(self.window, ElementSelector(name="Opzione", control_type="CheckBox"))
        self.assertEqual(
            self.adapter.describe_element(checkbox_after).toggle_state, "on",
            "il checkbox deve essere davvero spuntato, non solo la chiamata COM non sollevata",
        )


if __name__ == "__main__":
    unittest.main()
