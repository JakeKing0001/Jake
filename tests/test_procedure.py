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

from core.computer_agent import ComputerActionResult, ComputerAgent
from core.computer_use.browser_adapter import (
    BrowserNotFoundError,
    find_edge_executable,
    find_page_document,
    launch_isolated_browser,
)
from core.computer_use.procedure import (
    ACTION_CLICK,
    ACTION_TYPE,
    DRY_RUN_AMBIGUOUS,
    DRY_RUN_MISSING_PARAMETER,
    DRY_RUN_NOT_FOUND,
    DRY_RUN_WINDOW_NOT_FOUND,
    MissingParameterError,
    RecordedStep,
    UnknownActionError,
    dry_run_step,
    dry_run_steps,
    is_likely_drift,
    replay_step,
    replay_steps,
    substitute_parameters,
)
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter
from tests.test_ui_automation_adapter import _RealFixtureTestCase

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_WINDOW_TITLE = "Jake Computer Use Fixture"
_BROWSER_FIXTURE_URL = f"file:///{(_REPO_ROOT / 'benchmarks' / 'browser_fixture.html').as_posix()}"


def _edge_available() -> bool:
    try:
        find_edge_executable()
        return True
    except BrowserNotFoundError:
        return False



# Mouse e tastiera veri: mai input fuori dalla fixture (vedi tests/fixture_input_guard.py).
from tests.fixture_input_guard import install as setUpModule, uninstall as tearDownModule  # noqa: E402,F401

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


class SubstituteParametersTests(unittest.TestCase):
    """F3.8.2 (prima meta' - "parametri variabili"): `substitute_parameters`, una funzione pura -
    nessuna app reale necessaria per verificarla, a differenza del resto di questo file."""

    def test_a_text_without_placeholders_passes_through_unchanged(self):
        self.assertEqual(substitute_parameters("testo letterale", None), "testo letterale")
        self.assertEqual(substitute_parameters("testo letterale", {}), "testo letterale")

    def test_a_single_placeholder_is_substituted(self):
        result = substitute_parameters("Ciao ${nome}!", {"nome": "Jake"})
        self.assertEqual(result, "Ciao Jake!")

    def test_multiple_placeholders_are_all_substituted(self):
        result = substitute_parameters("${saluto} ${nome}!", {"saluto": "Ciao", "nome": "Jake"})
        self.assertEqual(result, "Ciao Jake!")

    def test_a_missing_parameter_raises_instead_of_leaving_the_placeholder_literal(self):
        """MAI scrivere il placeholder letterale (`${username}`) in un campo reale - un errore
        RUMOROSO invece di un'osservazione silenziosa solo dopo il fatto."""
        with self.assertRaises(MissingParameterError):
            substitute_parameters("Ciao ${nome}!", {"saluto": "Ciao"})

    def test_a_missing_parameter_with_no_parameters_at_all_still_raises(self):
        with self.assertRaises(MissingParameterError):
            substitute_parameters("Ciao ${nome}!", None)


class IsLikelyDriftTests(unittest.TestCase):
    """F3.8.6 (prima fetta - "rilevare drift"): una funzione pura su un `ComputerActionResult` gia'
    costruito - nessuna app reale necessaria, stesso principio di `SubstituteParametersTests`."""

    def test_a_successful_result_is_never_drift(self):
        self.assertFalse(is_likely_drift(ComputerActionResult(success=True)))

    def test_a_window_not_found_result_is_drift(self):
        self.assertTrue(is_likely_drift(ComputerActionResult(success=False, error="WINDOW_NOT_FOUND")))

    def test_a_not_found_result_is_drift(self):
        self.assertTrue(is_likely_drift(ComputerActionResult(success=False, error="NOT_FOUND")))

    def test_an_ambiguous_match_result_is_drift(self):
        self.assertTrue(is_likely_drift(ComputerActionResult(success=False, error="AMBIGUOUS_MATCH")))

    def test_a_policy_blocked_result_is_not_drift(self):
        """La policy ha bloccato un click sull'elemento GIUSTO - nessun cambiamento strutturale
        dell'app, un ri-registrare la procedura non risolverebbe nulla."""
        self.assertFalse(is_likely_drift(ComputerActionResult(success=False, error="POLICY_BLOCKED")))

    def test_an_operation_failed_result_is_not_drift(self):
        """L'elemento e' stato TROVATO ma l'azione e' fallita a livello di esecuzione - un
        fallimento diverso da "il selettore non risolve piu'"."""
        self.assertFalse(is_likely_drift(ComputerActionResult(success=False, error="OPERATION_FAILED")))

    def test_a_missing_parameter_result_is_not_drift(self):
        self.assertFalse(is_likely_drift(ComputerActionResult(success=False, error="MISSING_PARAMETER")))

    def test_a_failed_result_without_an_error_code_is_not_drift(self):
        """Caso limite difensivo - non dovrebbe accadere per un `ComputerActionResult` reale, ma
        `error=None` non deve mai essere interpretato come un codice di drift per coincidenza."""
        self.assertFalse(is_likely_drift(ComputerActionResult(success=False, error=None)))


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

    def test_a_parameterized_step_writes_the_substituted_value_for_real(self):
        """F3.8.2 (adozione): un passo REGISTRATO con `${nome}` scrive DAVVERO il valore passato a
        runtime, non il placeholder letterale - verificato osservando la fixture DOPO (l'elemento
        deve comparire con il testo SOSTITUITO), non solo che `replay_step` non abbia sollevato."""
        step = RecordedStep(
            action=ACTION_TYPE,
            selector=ElementSelector(
                automation_id="QApplication.jake_fixture_window.fixture_input",
                window_title_contains="Computer Use Fixture",
            ),
            text="elemento di ${utente}",
        )
        add_button = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
        )

        results = replay_steps(
            self.agent, self.adapter, [step, add_button], timeout_seconds=10.0, parameters={"utente": "Jake"},
        )

        self.assertTrue(all(r.success for r in results), results)
        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        engine = SelectorEngine(self.adapter)
        item = engine.find_unique(window, ElementSelector(name="elemento di Jake", control_type="ListItem"))
        self.assertEqual(item.name, "elemento di Jake")

    def test_a_missing_parameter_is_reported_without_ever_typing_the_literal_placeholder(self):
        step = RecordedStep(
            action=ACTION_TYPE,
            selector=ElementSelector(
                automation_id="QApplication.jake_fixture_window.fixture_input",
                window_title_contains="Computer Use Fixture",
            ),
            text="elemento di ${utente}",
        )

        result = replay_step(self.agent, self.adapter, step, timeout_seconds=5.0, parameters={"altro": "valore"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETER")
        # Verifica diretta del VALORE vero del campo (non `.name`, che e' solo l'etichetta
        # accessibile statica e non conterrebbe mai il placeholder comunque - lo stesso genere di
        # lettura gia' usata da browser_adapter.py::read_address_bar_text, F3.6.5).
        from comtypes.gen import UIAutomationClient as UIA

        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        engine = SelectorEngine(self.adapter)
        input_element = engine.find_unique_element(
            window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_input"),
        )
        value_pattern = input_element.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)
        self.assertNotIn("${utente}", value_pattern.CurrentValue)
        self.assertEqual(value_pattern.CurrentValue, "", "il campo deve restare vuoto, mai scritto a meta'")


@unittest.skipUnless(_edge_available(), "Microsoft Edge non e' installato in questo ambiente")
class ReplayAgainstARealBrowserPageTests(unittest.TestCase):
    """Scoperta empirica, non assunta (vedi ROADMAP_EXECUTION.md sezione F3.8): un `RecordedStep`
    (che richiede SEMPRE `selector.window_title_contains`, mai un `root=` gia' risolto - vedi il
    docstring della classe) funziona GIA' contro una pagina web reale, senza alcun codice nuovo in
    `procedure.py`. `replay_step` risolve la finestra per TITOLO (F3.7,
    `find_window_by_title_containing`) e la passa come `root=` a `click_element`/
    `type_into_element` (F3.4.2), la cui ricerca sottostante esplora TUTTI i discendenti del root -
    incluso il contenuto della pagina dentro il nodo `Document`, anche se `root` qui e' l'INTERA
    finestra del browser (chrome + pagina), non ristretto al `Document` come fa invece
    `core/computer_use/browser_adapter.py::find_page_document` (F3.6.1, che passa `root=document`
    esplicitamente). Il `<title>` della pagina fixture ("Jake Browser Fixture") compare nel titolo
    della finestra Edge - a differenza di un URL (normalizzato/imprevedibile, F3.6.5), resta
    STABILE finche' la pagina non cambia, rendendo `window_title_contains` gia' utilizzabile qui
    senza bisogno di svegliare esplicitamente l'albero di accessibilita' prima (la ricerca della
    finestra stessa non richiede il `Document`, solo `replay_step`/`click_element` in poi ne hanno
    bisogno, e lo svegliano da soli - verificato, non assunto).

    **Avvertenza reale, non solo un successo**: la ricerca avviene sull'INTERA finestra del
    browser, non solo sul `Document` - un selettore per SOLO `name`/`control_type` (senza
    `automation_id`) rischierebbe quindi, in linea di principio, di collidere con un elemento del
    chrome del browser (barra indirizzi, tab, bottoni) che condivide lo stesso nome/tipo. Mai
    osservato con QUESTA fixture (che usa `automation_id` univoci, l'attributo HTML `id` mappato
    direttamente da Chromium) - non ulteriormente mitigato qui, dichiarato onesto come limite noto
    invece di un problema silenzioso, coerente con F3.8.6 (rilevare drift), non ancora costruito."""

    def setUp(self):
        self.browser = launch_isolated_browser(_BROWSER_FIXTURE_URL)
        self.addCleanup(self.browser.terminate_and_cleanup)
        self.adapter = UIAutomationAdapter()
        self.window = self.adapter.find_window_by_process_id(self.browser.process.pid, timeout_seconds=15.0)
        self.agent = ComputerAgent()

    def test_a_recorded_procedure_replays_for_real_against_a_real_browser_page(self):
        steps = [
            RecordedStep(
                action=ACTION_TYPE,
                selector=ElementSelector(automation_id="fixture-input", window_title_contains="Jake Browser Fixture"),
                text="dalla procedura",
            ),
            RecordedStep(
                action=ACTION_CLICK,
                selector=ElementSelector(automation_id="fixture-add-button", window_title_contains="Jake Browser Fixture"),
            ),
        ]

        results = replay_steps(self.agent, self.adapter, steps, timeout_seconds=10.0)

        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.success for r in results), results)

        # Verifica diretta e indipendente dell'effetto reale (non solo che le chiamate non
        # abbiano sollevato) - stesso principio gia' seguito da
        # tests/test_browser_adapter.py::test_computer_agent_click_and_type_work_against_the_document_root.
        document = find_page_document(self.adapter, self.window)
        matches = self.adapter.find_matching_elements(document, name="dalla procedura")
        self.assertEqual(len(matches), 1, "il paragrafo di output deve mostrare davvero il testo digitato")


class DryRunAgainstTheRealFixtureTests(_RealFixtureTestCase):
    """F3.8.4 (prima fetta - "testarla in dry-run"): `dry_run_step`/`dry_run_steps` non devono MAI
    cliccare/scrivere - un solo processo fixture CONDIVISO per l'intera classe (a differenza di
    `ReplayAgainstTheRealFixtureTests`, che muta lo stato e ha bisogno di un processo dedicato per
    test) e' quindi sicuro: nessun test qui cambia lo stato della fixture. Riusa
    `_RealFixtureTestCase` (`tests/test_ui_automation_adapter.py`) invece di duplicare setUp/
    tearDown a mano - **buco reale trovato scrivendo QUESTO test, non ipotizzato**: una prima
    versione duplicava la stessa logica ma SENZA il `try`/`except` attorno a `find_window_by_title`
    che `_RealFixtureTestCase` gia' ha - quando quella ricerca ha sollevato (un `WindowNotFoundError`
    dopo 15s, coerente con un carico di sistema insolito su questa macchina dopo un'intera sessione
    di lanci reali di app), il processo fixture gia' avviato e' rimasto ORFANO (mai terminato,
    `tearDownClass` non viene chiamato da `unittest` se `setUpClass` solleva) - quella finestra
    orfana ha poi fatto fallire `ReplayAgainstTheRealFixtureTests` con un'ambiguita' reale (due
    finestre "Computer Use Fixture" invece di una). Riusare la classe base gia' corretta invece di
    duplicarla evita la stessa classe di bug per costruzione."""

    def test_a_step_that_would_resolve_reports_would_succeed_true(self):
        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
        )

        result = dry_run_step(self.adapter, step, timeout_seconds=5.0)

        self.assertTrue(result.would_succeed)
        self.assertIsNone(result.error)

    def test_a_step_with_a_missing_element_reports_would_succeed_false_with_not_found(self):
        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Questo bottone non esiste XYZ", window_title_contains="Computer Use Fixture"),
        )

        result = dry_run_step(self.adapter, step, timeout_seconds=1.0)

        self.assertFalse(result.would_succeed)
        self.assertTrue(result.error.startswith(DRY_RUN_NOT_FOUND))

    def test_a_step_with_a_missing_window_reports_would_succeed_false_with_window_not_found(self):
        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", window_title_contains="Finestra che non esiste XYZ123"),
        )

        result = dry_run_step(self.adapter, step, timeout_seconds=1.0)

        self.assertFalse(result.would_succeed)
        self.assertTrue(result.error.startswith(DRY_RUN_WINDOW_NOT_FOUND))

    def test_an_ambiguous_step_reports_would_succeed_false_with_ambiguous(self):
        """'Categoria A'/'Categoria B' condividono lo stesso control_type - la stessa ambiguita'
        gia' nota della fixture, usata altrove in questa sessione (F3.3.2)."""
        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(control_type="TreeItem", window_title_contains="Computer Use Fixture"),
        )

        result = dry_run_step(self.adapter, step, timeout_seconds=1.0)

        self.assertFalse(result.would_succeed)
        self.assertTrue(result.error.startswith(DRY_RUN_AMBIGUOUS))

    def test_a_step_needing_a_missing_parameter_reports_would_succeed_false(self):
        """F3.8.2 (adozione): il selettore da solo risolverebbe (il campo esiste) - un dry-run che
        controllasse SOLO il selettore darebbe un falso senso di sicurezza, dato che il replay
        vero fallirebbe comunque per il parametro mancante."""
        step = RecordedStep(
            action=ACTION_TYPE,
            selector=ElementSelector(
                automation_id="QApplication.jake_fixture_window.fixture_input",
                window_title_contains="Computer Use Fixture",
            ),
            text="elemento di ${utente}",
        )

        result = dry_run_step(self.adapter, step, timeout_seconds=1.0, parameters=None)

        self.assertFalse(result.would_succeed)
        self.assertTrue(result.error.startswith(DRY_RUN_MISSING_PARAMETER))

    def test_dry_run_step_never_actually_clicks_anything(self):
        """Il punto centrale del dry-run: cliccare "Aggiungi" via dry-run NON deve aggiungere
        nulla alla lista - verificato osservando la fixture DOPO, non solo assumendo dal
        `would_succeed`. **Buco reale trovato scrivendo questo test, non ipotizzato**: la fixture
        ha DUE liste (`item_list`, dove "Aggiungi" aggiunge davvero, e `scroll_list`, pre-popolata
        con "Riga 1".."Riga 30" fin dall'avvio, F3.1.1) - cercare `ListItem` in tutta la finestra
        trova SEMPRE le righe della scroll_list, un falso positivo che farebbe fallire questo test
        anche se il dry-run si comportasse correttamente. Scoperto per davvero il proprio bug
        (non assunto): il fallimento iniziale mostrava "Riga 1"/"Riga 2"/"Riga 3", mai il nome
        digitato dal test - la prova che il dry-run non aveva cliccato per davvero, solo che il
        test cercava nel posto sbagliato. Corretto scoprendo prima la lista GIUSTA per
        `automation_id` (`fixture_list`, non `fixture_scroll_list`)."""
        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
        )

        dry_run_step(self.adapter, step, timeout_seconds=5.0)

        engine = SelectorEngine(self.adapter)
        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        item_list = engine.find_unique_element(
            window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_list"),
        )
        items = engine.find_all(item_list, ElementSelector(control_type="ListItem"))
        self.assertEqual(items, [], "un dry-run non deve MAI eseguire l'azione vera")

    def test_dry_run_steps_checks_every_step_even_after_an_earlier_failure(self):
        """A differenza di `replay_steps` (si ferma al primo fallimento), un dry-run verifica
        TUTTI i passi anche dopo uno che non risolverebbe - nessuna azione vera che potrebbe
        rendere i passi successivi dipendenti da uno stato mai raggiunto."""
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

        results = dry_run_steps(self.adapter, steps, timeout_seconds=1.0)

        self.assertEqual(len(results), 2, "un dry-run deve controllare OGNI passo, non fermarsi al primo fallimento")
        self.assertFalse(results[0].would_succeed)
        self.assertTrue(results[1].would_succeed)


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


class RiskIntentTests(unittest.TestCase):
    """F3.4.3 ("richiedere policy prima di upload, submit, send, delete e purchase"), adozione in
    F3.8: `RecordedStep.risk_intent` inoltrato da `replay_step`/`dry_run_step` a `ComputerAgent`,
    che decide davvero (nessuna decisione duplicata qui). Un vero `PolicyEngine` (economico da
    costruire, nessun mock del motore stesso), stesso principio gia' seguito da
    `tests/test_computer_agent.py::PolicyIntegrationTests`."""

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

    def test_a_round_trip_preserves_risk_intent_too(self):
        original = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", window_title_contains="Fixture"),
            risk_intent="DELETE_PATH",
        )
        restored = RecordedStep.from_dict(original.to_dict())
        self.assertEqual(original, restored)
        self.assertEqual(restored.risk_intent, "DELETE_PATH")

    def test_replay_step_is_blocked_by_policy_without_ever_touching_the_button(self):
        from core.policy_engine import PolicyEngine

        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
            risk_intent="DELETE_PATH",
        )
        engine = PolicyEngine(blocked_intents={"DELETE_PATH"})
        agent = ComputerAgent(policy_engine=engine)

        result = replay_step(agent, self.adapter, step, timeout_seconds=5.0)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "POLICY_BLOCKED")
        # Verifica diretta: il click non deve MAI essere arrivato all'app - la lista resta vuota.
        window = self.adapter.find_window_by_title(_FIXTURE_WINDOW_TITLE, timeout_seconds=5.0)
        engine_selector = SelectorEngine(self.adapter)
        item_list = engine_selector.find_unique_element(
            window, ElementSelector(automation_id="QApplication.jake_fixture_window.fixture_list"),
        )
        self.assertEqual(engine_selector.find_all(item_list, ElementSelector(control_type="ListItem")), [])

    def test_replay_step_proceeds_for_real_once_confirmed(self):
        from core.policy_engine import PolicyEngine

        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
            risk_intent="DELETE_PATH",
        )
        engine = PolicyEngine(always_confirm_intents={"DELETE_PATH"})
        agent = ComputerAgent(policy_engine=engine)

        result = replay_step(agent, self.adapter, step, timeout_seconds=5.0, policy_parameters={"confirmed": True})

        self.assertTrue(result.success, result)

    def test_dry_run_step_reports_a_blocked_intent_without_an_agent_it_would_not_know(self):
        """Senza `agent` (il default), il dry-run non puo' sapere della policy - comportamento
        invariato, non un fallimento per un pre-requisito mancante."""
        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
            risk_intent="DELETE_PATH",
        )

        result = dry_run_step(self.adapter, step, timeout_seconds=5.0)

        self.assertTrue(result.would_succeed)

    def test_dry_run_step_reports_a_blocked_intent_when_an_agent_is_given(self):
        """Il punto centrale: un dry-run che controllasse SOLO il selettore darebbe un falso senso
        di sicurezza per un passo che il replay vero bloccherebbe per policy."""
        from core.policy_engine import PolicyEngine

        step = RecordedStep(
            action=ACTION_CLICK,
            selector=ElementSelector(name="Aggiungi", control_type="Button", window_title_contains="Computer Use Fixture"),
            risk_intent="DELETE_PATH",
        )
        engine = PolicyEngine(blocked_intents={"DELETE_PATH"})
        agent = ComputerAgent(policy_engine=engine)

        result = dry_run_step(self.adapter, step, timeout_seconds=5.0, agent=agent)

        self.assertFalse(result.would_succeed)
        self.assertEqual(result.error, "POLICY_BLOCKED")


if __name__ == "__main__":
    unittest.main()
