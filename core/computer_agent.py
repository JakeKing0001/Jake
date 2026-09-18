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
