"""Controller unificato per le azioni sullo schermo (v3.7, Computer Use Engine): osserva
(OCR), localizza (trova il testo/elemento), agisce (click) e verifica (e' cambiato qualcosa?)
come UNA pipeline coerente in un solo posto, invece della stessa logica sparsa e duplicata tra
skill isolate (CLICK_TEXT, CLICK_ELEMENT) che agiscono ognuna per conto proprio.

Il "recupero" (ritentare, cambiare strategia) resta deliberatamente una decisione dell'agente a
passi (core/agent.py, fase 3.3) che vede l'osservazione e ragiona sul da farsi, non un
automatismo qui dentro: ricliccare alla cieca quando lo schermo non sembra cambiato rischierebbe
di attivare due volte un'azione che in realta' era gia' andata a buon fine (es. l'invio di un
modulo, l'attivazione di una casella di spunta), il che sarebbe peggio di riportare
onestamente "non verificato" e lasciare che sia l'agente a decidere il prossimo passo.

Oggi copre solo il click (la parte piu' soggetta ad ambiguita': coordinate leggermente fuori
bersaglio, bottoni disabilitati); digitazione e scorrimento restano skill isolate (TYPE_TEXT,
PRESS_KEY, SCROLL) e sono un possibile prossimo passo di questa fase.

`click_element` (F3.4.2, prima fetta - "unificare click... nel ComputerAgent", adozione): trova
un elemento per nome/ruolo/automation_id DENTRO una finestra data (F3.3, `SelectorEngine`) e lo
clicca semanticamente tramite il pattern Invoke di UI Automation (F3.4, `ActionExecutor`) invece
di coordinate pixel ASSOLUTE fornite dal chiamante - le coordinate restano un dettaglio interno,
LETTE da UI Automation, non indovinate ne' passate dall'esterno come in `click_point`. Se Invoke
non e' disponibile o non produce un effetto visibile, ripiega su un click pixel alle STESSE
coordinate lette da UI Automation (F3.5, `try_strategies_in_order`) - non un secondo metodo
separato, la stessa scala di ripiego gia' costruita e testata in questa sessione. Nessun campo
`ElementSelector`/pattern e' passato dal chiamante oltre nome/ruolo/automation_id: un primo
gradino deliberatamente per il caso piu' comune (bottoni/link, dove Invoke e' gia' verificato
affidabile in F3.4) - elementi dove Invoke NON si applica (es. una voce di lista che va
selezionata, non "premuta") restano fuori da questo metodo, non affrontati qui.

**Assunzione dichiarata esplicitamente, NON verificata empiricamente per Invoke** (a differenza
della scoperta gia' fatta per SelectionItem, vedi `core/computer_use/fallback.py`): incatenare un
tentativo Invoke fallito prima di un click pixel sullo STESSO elemento potrebbe in teoria
"avvelenare" lo stato allo stesso modo gia' trovato per SelectionItem su un `QListWidgetItem` -
non ancora messo alla prova con un test dedicato per Invoke specificamente, quindi la strategia
Invoke qui NON e' marcata `unsafe_after_failure` (il comportamento di default, incatenare) invece
di assumere il limite peggiore senza prova. Se un futuro test dovesse trovare lo stesso
avvelenamento anche per Invoke, questa scelta andrebbe rivista.

La VERIFICA resta la stessa evidenza DEBOLE gia' dichiarata in F3.5.5 (pixel diff, `evidence=
EVIDENCE_PIXEL_DIFF`) - questo metodo non conosce il dominio dell'app target, a differenza dei
test end-to-end di F3.1-F3.5 (ognuno con un secondo segnale indipendente specifico del caso, es.
il bottone "Rimuovi selezionato" che si abilita). I metodi esistenti (`click_text`/`click_point`/
`locate_text`/`observe`) restano INVARIATI - `click_element` e' additivo, non ancora usato da
nessuna skill esistente (CLICK_TEXT/CLICK_ELEMENT restano sul vecchio percorso a coordinate pixel/
OCR - collegarli e' una decisione di adozione a parte, non affrontata qui).

`evidence` (F3.5.5, "usare pixel diff soltanto come evidenza debole", adozione): `verified=True`
qui viene SEMPRE da un pixel diff (`core/vision/screen_diff.py`), l'UNICO segnale disponibile a
questa classe - a differenza della verifica basata su UI Automation costruita in `core/
computer_use/` (F3.2-F3.5) contro la fixture (es. leggere se il bottone "Rimuovi selezionato" e'
davvero abilitato), un pixel diff non legge NULLA dello stato reale dell'applicazione, solo se i
pixel sullo schermo sono cambiati. E' un'evidenza DEBOLE in entrambe le direzioni: ne' necessaria
(un click puo' avere un effetto reale senza alcun cambiamento visibile, es. un link verso una
pagina gia' aperta - gia' gestito da questa classe, che riporta onestamente `verified=False` senza
far fallire il click) ne' sufficiente (un cursore che lampeggia, un orologio che avanza, una
qualunque animazione indipendente dal click potrebbero far cambiare i pixel senza che il click
abbia avuto l'effetto voluto - un falso positivo che questa classe non puo' distinguere da un vero
successo). Il campo rende esplicita la FONTE della verifica invece di lasciare che un futuro
chiamante legga `verified=True` come se fosse equivalente a una verifica basata su stato reale
dell'app - non lo e' mai, in questa classe."""
import time
from dataclasses import dataclass

POST_ACTION_SETTLE_SECONDS = 0.4

# F3.5.5: vocabolario chiuso per ComputerActionResult.evidence - vedi il docstring del modulo.
EVIDENCE_PIXEL_DIFF = "pixel_diff"
EVIDENCE_NONE = "none"


@dataclass
class ComputerActionResult:
    success: bool
    x: int | None = None
    y: int | None = None
    matched: str | None = None
    verified: bool = False
    change_ratio: float = 0.0
    error: str | None = None
    # F3.5.5: EVIDENCE_NONE quando nessun controllo e' stato possibile (es. la cattura schermo
    # iniziale e' fallita) - mai EVIDENCE_PIXEL_DIFF per un controllo che non e' davvero avvenuto.
    evidence: str = EVIDENCE_NONE


class ComputerAgent:
    def observe(self) -> list[dict] | None:
        """OCR dello schermo attuale: ogni parola visibile con il suo rettangolo in pixel."""
        from core.vision.screen import read_screen_words

        return read_screen_words()

    def locate_text(self, text: str, words: list[dict] | None = None) -> dict | None:
        """Trova 'text' tra le parole osservate (le rilegge da sola se non gia' fornite)."""
        from skills.screen_click import find_text_on_screen

        if words is None:
            words = self.observe()
        if not words:
            return None
        return find_text_on_screen(text, words)

    def click_text(self, text: str, button: str = "left") -> ComputerActionResult:
        hit = self.locate_text(text)
        if hit is None:
            return ComputerActionResult(success=False, error="NOT_FOUND")
        return self.click_point(hit["x"], hit["y"], button, matched=hit["matched"])

    def click_point(self, x: int, y: int, button: str = "left", matched: str | None = None) -> ComputerActionResult:
        from core.vision.screen import capture_screenshot_image
        from core.vision.screen_diff import pixel_change_ratio, screen_visibly_changed

        try:
            before = capture_screenshot_image()
        except Exception:
            before = None

        try:
            import pyautogui
            if button == "double":
                pyautogui.doubleClick(x, y)
            else:
                pyautogui.click(x, y, button="right" if button == "right" else "left")
        except Exception:
            return ComputerActionResult(success=False, error="OPERATION_FAILED")

        verified, ratio, evidence = False, 0.0, EVIDENCE_NONE
        if before is not None:
            try:
                time.sleep(POST_ACTION_SETTLE_SECONDS)
                after = capture_screenshot_image()
                ratio = pixel_change_ratio(before, after)
                verified = screen_visibly_changed(before, after)
                evidence = EVIDENCE_PIXEL_DIFF
            except Exception:
                pass
        return ComputerActionResult(
            success=True, x=x, y=y, matched=matched, verified=verified, change_ratio=round(ratio, 4),
            evidence=evidence,
        )

    def click_element(
        self, *, window_title: str, name: str | None = None, control_type: str | None = None,
        automation_id: str | None = None, timeout_seconds: float = 5.0,
    ) -> ComputerActionResult:
        """F3.4.2: trova un elemento per nome/ruolo/automation_id dentro `window_title` e lo
        clicca via UI Automation (Invoke, F3.4), con ripiego a un click pixel alle stesse
        coordinate se Invoke fallisce o non ha un effetto visibile (F3.5). Vedi il docstring del
        modulo per le scelte e i limiti dichiarati."""
        from core.computer_use.executor import ActionExecutor
        from core.computer_use.fallback import try_strategies_in_order
        from core.computer_use.selector import AmbiguousSelectionError, ElementSelector, NoMatchError, SelectorEngine
        from core.computer_use.ui_automation_adapter import UIAutomationAdapter, WindowNotFoundError
        from core.vision.screen import capture_screenshot_image
        from core.vision.screen_diff import pixel_change_ratio, screen_visibly_changed

        adapter = UIAutomationAdapter()
        try:
            window = adapter.find_window_by_title(window_title, timeout_seconds=timeout_seconds)
        except WindowNotFoundError:
            return ComputerActionResult(success=False, error="WINDOW_NOT_FOUND")

        engine = SelectorEngine(adapter)
        selector = ElementSelector(name=name, control_type=control_type, automation_id=automation_id)
        try:
            element = engine.wait_for_unique_element(window, selector, timeout_seconds=timeout_seconds)
        except NoMatchError:
            return ComputerActionResult(success=False, error="NOT_FOUND")
        except AmbiguousSelectionError:
            return ComputerActionResult(success=False, error="AMBIGUOUS_MATCH")

        info = adapter.describe_element(element)
        if info is None:
            return ComputerActionResult(success=False, error="OPERATION_FAILED")
        left, top, width, height = info.bounds
        center_x, center_y = left + width // 2, top + height // 2
        executor = ActionExecutor()

        try:
            before = capture_screenshot_image()
        except Exception:
            before = None
        last_ratio = [0.0]
        # F3.5.5/click_point: "success" (l'azione e' stata davvero ESEGUITA, mai sollevato) resta
        # un fatto diverso da "verified" (l'evidenza debole del pixel diff l'ha confermato) -
        # stessa distinzione gia' seguita da click_point, un click legittimo che non cambia nulla
        # di visibile (es. un link verso una pagina gia' aperta) non deve diventare un fallimento.
        action_performed = [False]

        def _verify() -> bool:
            if before is None:
                return False
            try:
                time.sleep(POST_ACTION_SETTLE_SECONDS)
                after = capture_screenshot_image()
                last_ratio[0] = pixel_change_ratio(before, after)
                return screen_visibly_changed(before, after)
            except Exception:
                return False

        def _uia_invoke() -> None:
            executor.invoke(element)
            action_performed[0] = True

        def _pixel_click() -> None:
            import pyautogui
            pyautogui.click(center_x, center_y)
            action_performed[0] = True

        outcome = try_strategies_in_order(
            [("uia_invoke", _uia_invoke), ("pixel_click", _pixel_click)],
            verify=_verify,
        )

        if not action_performed[0]:
            return ComputerActionResult(success=False, error="OPERATION_FAILED")
        return ComputerActionResult(
            success=True, x=center_x, y=center_y, matched=name,
            verified=outcome.succeeded, change_ratio=round(last_ratio[0], 4),
            evidence=EVIDENCE_PIXEL_DIFF if before is not None else EVIDENCE_NONE,
        )
