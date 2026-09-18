"""Verifica visiva (F3.5.1, il gradino "OCR" della scala di ripiego - dichiarato dall'ordine
"API/app adapter -> UIA -> browser DOM -> OCR -> vision -> coordinate", MAI costruito prima
d'ora). Motivato da un buco reale trovato indagando Task 3/10 di F3.1.2 ("espandi categoria",
vedi ROADMAP_EXECUTION.md): un doppio click reale su un `QTreeWidgetItem` lo espande DAVVERO a
livello Qt (pixel diff positivo verificato), ma UI Automation non rivela MAI i suoi figli - non
un problema dell'azione (che funziona), della VERIFICA. `describe_tree()` (F3.2) resta cieco;
l'unico segnale indipendente rimasto e' visivo.

`word_visible_in_window` legge SOLO la porzione di schermo dentro i bordi della finestra data
(le coordinate vengono da UI Automation, F3.2 - `describe_element(window).bounds`), mai lo
schermo intero - una scelta deliberata di privacy, non solo di precisione: uno screenshot
dell'intero desktop potrebbe catturare contenuto REALE dell'utente estraneo alla finestra
osservata (scoperto empiricamente durante l'indagine che ha motivato questo modulo: un ritaglio
sbagliato ha catturato per un istante lo sfondo del desktop, mai salvato ne' ispezionato oltre
la cancellazione immediata). Il ritaglio viene scritto in un file TEMPORANEO, letto una sola
volta da `read_screen_words` (l'OCR di `core/vision/screen.py` accetta solo un percorso, non
un'immagine PIL in memoria) e cancellato subito dopo, mai lasciato in `data/screenshots/`.

Deliberatamente NON affrontati qui, passi successivi dichiarati (stesso principio "un incremento
alla volta" di questa sessione):
- corrispondenza di una FRASE multi-parola (oggi solo una singola parola OCR esatta - l'OCR di
  Windows spezza il testo in parole indipendenti, es. "Categoria A" diventa due parole distinte
  "Categoria"/"A" nel dump gia' osservato in questa sessione; ricomporre frasi e' un problema a
  se', non affrontato qui);
- una posizione/rettangolo del testo trovato (oggi solo un booleano "presente/assente", non le
  coordinate per un click - quello resta compito di `ComputerAgent.locate_text`, gia' esistente,
  non duplicato qui);
- collegamento a `try_strategies_in_order` come un gradino DICHIARATO della scala (oggi e' una
  funzione libera che un chiamante puo' gia' passare come `verify`, non ancora un terzo elemento
  automatico della lista di strategie).

`word_visible_in_window_eventually` (fix di un fallimento REALE in CI, non ipotizzato): il test
end-to-end di Task 3 costruito su `word_visible_in_window` passava in modo ripetuto e affidabile
in locale (3/3 esecuzioni) ma e' fallito sul runner CI (GitHub Actions windows-latest) - un
rendering piu' lento su una macchina condivisa puo' far si' che un singolo controllo OCR, fatto
UNA volta subito dopo l'azione, arrivi PRIMA che Qt abbia finito di ridisegnare i figli appena
rivelati. Stesso principio "polling con timeout invece di un singolo tentativo ottimistico" gia'
seguito da `SelectorEngine.wait_for_unique_element` (F3.4.7), qui applicato all'OCR invece che a
UI Automation."""
import tempfile
import time
from pathlib import Path


def word_visible_in_window(adapter, window, word: str) -> bool:
    """Vero se `word` (una singola parola, corrispondenza ESATTA con un token OCR - non una
    sottostringa, non una frase) compare da qualche parte dentro i bordi di `window`. `False`
    onesto (mai un'eccezione) sia se la finestra non e' piu' leggibile (F3.2, stesso principio di
    `describe_element`) sia se l'OCR non e' disponibile per il profilo lingua dell'utente
    (`read_screen_words` gia' restituisce `None` in quel caso, F3.0)."""
    from core.vision.screen import capture_screenshot_image, read_screen_words

    info = adapter.describe_element(window)
    if info is None:
        return False
    left, top, width, height = info.bounds
    screenshot = capture_screenshot_image()
    crop = screenshot.crop((left, top, left + width, top + height))

    with tempfile.TemporaryDirectory() as tmp_dir:
        crop_path = Path(tmp_dir) / "window_crop.png"
        crop.save(crop_path)
        words = read_screen_words(image_path=crop_path)

    if words is None:
        return False
    return any(entry["text"] == word for entry in words)


def word_visible_in_window_eventually(adapter, window, word: str, timeout_seconds: float = 3.0) -> bool:
    """Come `word_visible_in_window`, ma RITENTA con un breve intervallo fino al timeout invece
    di un singolo controllo OCR - un rendering lento (una macchina CI condivisa, non la propria)
    puo' far si' che il primo controllo, fatto subito dopo l'azione, arrivi prima che l'interfaccia
    abbia finito di aggiornarsi. `False` onesto se `word` non appare mai entro il timeout, mai
    un'eccezione."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        if word_visible_in_window(adapter, window, word):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
