"""Confronto tra due screenshot (v3.6, fase Vision 2.0): quanto e' cambiato lo schermo tra un
prima e un dopo. E' il pezzo che mancava per far "osservare" a Jake l'effetto reale di un'azione
sullo schermo (un click, un tasto premuto) invece di fidarsi ciecamente che pyautogui non abbia
sollevato un'eccezione - la stessa filosofia di core/execution_safety.py (v3.3) applicata allo
schermo invece che al filesystem, e un mattone per il futuro Computer Use Engine (fase 3.7,
"ogni azione verificata contro il cambiamento atteso dell'interfaccia")."""
from PIL import Image, ImageChops

# Sotto questa soglia il cambiamento e' considerato rumore (compressione, cursore lampeggiante,
# un orologio che aggiorna un pixel), non un vero cambiamento visivo dell'interfaccia.
DEFAULT_CHANGE_RATIO_THRESHOLD = 0.01


def pixel_change_ratio(before: Image.Image, after: Image.Image, per_pixel_threshold: int = 24) -> float:
    """Frazione di pixel (0.0-1.0) cambiati in modo percepibile tra due screenshot.
    per_pixel_threshold ignora variazioni minime di luminosita' (antialiasing, compressione)
    che non sono un vero cambiamento visivo, contandole come rumore invece che come cambiamento."""
    if before.size != after.size:
        after = after.resize(before.size)
    diff = ImageChops.difference(before.convert("L"), after.convert("L"))
    histogram = diff.histogram()
    total_pixels = before.size[0] * before.size[1]
    if total_pixels == 0:
        return 0.0
    changed = sum(count for level, count in enumerate(histogram) if level > per_pixel_threshold)
    return changed / total_pixels


def screen_visibly_changed(before: Image.Image, after: Image.Image, threshold: float = DEFAULT_CHANGE_RATIO_THRESHOLD) -> bool:
    return pixel_change_ratio(before, after) >= threshold
