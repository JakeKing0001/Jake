"""Test per core/computer_use/procedure.py (F3.8.1, prima fetta di F3.8 - "Learn by demonstration",
mai iniziata prima d'ora). Un test di unita' (validazione di `RecordedStep`) + un test end-to-end
REALE contro la fixture Qt gia' esistente (F3.1.1): registra una procedura, la fa passare
DAVVERO per `to_dict`/`from_dict` (non un `RecordedStep` costruito a mano nel test, che non
proverebbe il caso reale "salva su disco, ricarica in un momento diverso"), poi la rigioca - lo
stesso principio "prova il percorso vero, non solo i pezzi" gia' seguito da
`tests/test_selector.py::LocateTests`."""
import subprocess
import sys
import unittest
from pathlib import Path

from core.computer_agent import ComputerAgent
from core.computer_use.procedure import (
    ACTION_CLICK,
    ACTION_TYPE,
    RecordedStep,
    UnknownActionError,
    replay_step,
    replay_steps,
)
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_WINDOW_TITLE = "Jake Computer Use Fixture"


class RecordedStepValidationTests(unittest.TestCase):
    def test_an_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            RecordedStep(
                action="doppio_click", selector=ElementSelector(name="Aggiungi", window_title_contains="Fixture"),
            )

    def test_a_selector_without_a_window_criterion_is_rejected(self):
        """Un passo REGISTRATO deve poter essere rigiocato senza alcuna finestra gia' risolta a
        portata di mano - a differenza di un `ElementSelector` usato al volo, qui il criterio
        della finestra e' obbligatorio, non opzionale."""
        with self.assertRaises(ValueError):
            RecordedStep(action=ACTION_CLICK, selector=ElementSelector(name="Aggiungi"))

    def test_a_type_action_without_text_is_rejected(self):
        with self.assertRaises(ValueError):
            RecordedStep(
                action=ACTION_TYPE, selector=ElementSelector(name="Campo", window_title_contains="Fixture"),
            )

    def test_a_click_action_with_text_is_rejected(self):
        """Un click non scrive nulla - dargli un `text` sarebbe un passo ambiguo (quale delle due
        azioni intende davvero il chiamante?), rifiutato invece di ignorare silenziosamente il
        campo in eccesso."""
        with self.assertRaises(ValueError):
            RecordedStep(
                action=ACTION_CLICK, selector=ElementSelector(name="Aggiungi", window_title_contains="Fixture"),
                text="questo non dovrebbe esserci",
            )

    def test_a_round_trip_through_dict_reproduces_an_identical_step(self):
        original = RecordedStep(
            action=ACTION_TYPE, selector=ElementSelector(name="Campo", window_title_contains="Fixture"),
            text="contenuto di prova",
        )
        restored = RecordedStep.from_dict(original.to_dict())
        self.assertEqual(original, restored)

    def test_to_dict_omits_text_for_a_click_step(self):
        step = RecordedStep(action=ACTION_CLICK, selector=ElementSelector(name="Aggiungi", window_title_contains="Fixture"))
        self.assertNotIn("text", step.to_dict())

    def test_from_dict_rejects_an_unknown_key(self):
        with self.assertRaises(ValueError):
            RecordedStep.from_dict({
                "action": ACTION_CLICK,
                "selector": {"name": "Aggiungi", "window_title_contains": "Fixture"},
                "testo": "typo reale, non 'text'",
            })

    def test_from_dict_requires_action_and_selector(self):
        with self.assertRaises(ValueError):
            RecordedStep.from_dict({"selector": {"name": "Aggiungi", "window_title_contains": "Fixture"}})


class ReplayAgainstTheRealFixtureTests(unittest.TestCase):
    """End-to-end reale: NESSUno stato condiviso con altri file di test (la procedura AGGIUNGE un
    elemento alla lista, mutando la fixture - un processo dedicato per classe, stesso principio
    gia' seguito da `tests/test_selector.py::LocalizationAfterResizeAndMoveTests`)."""

    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        self.addCleanup(self._terminate_process)
        self.adapter = UIAutomationAdapter()
        self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)  # attende che sia visibile
        self.agent = ComputerAgent()

    def _terminate_process(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def test_a_recorded_procedure_saved_and_reloaded_from_disk_replays_for_real(self):
        """Il caso motivante del modulo: registra due passi (scrivi, poi clicca "Aggiungi"),
        fa DAVVERO il giro to_dict -> [simulato "su disco"] -> from_dict, poi rigioca contro la
        fixture VERA - verifica che l'elemento sia comparso DAVVERO in lista, non solo che le
        chiamate non abbiano sollevato."""
        recorded = [
            RecordedStep(
                action=ACTION_TYPE,
                selector=ElementSelector(
                    automation_id="QApplication.jake_fixture_window.fixture_input",
                    window_title_contains="Computer Use Fixture",
                ),
                text="elemento da procedura registrata",
            ),
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
            ),
        ]
        saved = [step.to_dict() for step in recorded]

        reloaded = [RecordedStep.from_dict(data) for data in saved]
        results = replay_steps(self.agent, self.adapter, reloaded, timeout_seconds=10.0)

        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.success for r in results), results)

        engine = SelectorEngine(self.adapter)
        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        item = engine.find_unique(window, ElementSelector(name="elemento da procedura registrata", control_type="ListItem"))
        self.assertEqual(item.name, "elemento da procedura registrata")

    def test_replay_steps_stops_at_the_first_failure_not_the_later_ones(self):
        """Un secondo passo con un selettore che non trovera' mai nulla (nome inventato) non deve
        essere raggiunto - la lista di risultati deve fermarsi al primo fallimento, non contenere
        un terzo risultato per un passo mai tentato."""
        steps = [
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(name="Questo bottone non esiste XYZ", window_title_contains="Computer Use Fixture"),
            ),
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
            ),
        ]

        results = replay_steps(self.agent, self.adapter, steps, timeout_seconds=2.0)

        self.assertEqual(len(results), 1, "il secondo passo non deve mai essere tentato dopo il fallimento del primo")
        self.assertFalse(results[0].success)

    def test_replay_step_with_a_window_that_does_not_exist_reports_window_not_found(self):
        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", window_title_contains="Finestra che non esiste XYZ123"),
        )

        result = replay_step(self.agent, self.adapter, step, timeout_seconds=1.0)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "WINDOW_NOT_FOUND")

    def test_replay_survives_the_window_being_resized_between_recording_and_replay(self):
        """F3.3.7 (adozione): la procedura registrata usa un selettore per NOME, non coordinate -
        deve funzionare anche se la finestra e' stata ridimensionata/spostata dopo la
        registrazione, esattamente come gia' dimostrato per un click singolo in F3.3.7."""
        from comtypes.gen import UIAutomationClient as UIA

        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        pattern = window.GetCurrentPattern(UIA.UIA_TransformPatternId)
        transform = pattern.QueryInterface(UIA.IUIAutomationTransformPattern)
        transform.Resize(900, 700)
        transform.Move(60, 60)

        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
        )

        result = replay_step(self.agent, self.adapter, step, timeout_seconds=5.0)

        self.assertTrue(result.success, result)


class UnknownActionErrorTests(unittest.TestCase):
    """`RecordedStep.__post_init__` gia' impedisce di costruire un'azione sconosciuta tramite il
    costruttore/`from_dict` (entrambi gia' testati sopra) - questo verifica solo la difesa
    ESPLICITA in piu' dentro `replay_step` stesso, per un oggetto che ha aggirato quella
    validazione (`object.__new__` + `object.__setattr__`, l'unico modo di costruire un'istanza
    di un dataclass `frozen=True` senza passare da `__init__`/`__post_init__`)."""

    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "30"],
            cwd=str(_REPO_ROOT),
        )
        self.addCleanup(self._terminate_process)
        self.adapter = UIAutomationAdapter()
        self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=15.0)

    def _terminate_process(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def test_replay_step_raises_for_a_step_that_bypassed_validation(self):
        valid_selector = ElementSelector(name="Aggiungi", window_title_contains="Computer Use Fixture")
        forged = object.__new__(RecordedStep)
        object.__setattr__(forged, "action", "vola_via")
        object.__setattr__(forged, "selector", valid_selector)
        object.__setattr__(forged, "text", None)

        with self.assertRaises(UnknownActionError):
            replay_step(ComputerAgent(), self.adapter, forged, timeout_seconds=5.0)


if __name__ == "__main__":
    unittest.main()
