"""Controllo del PC "a vista" (v3.0): cliccare dove dice l'utente, scorrere.

CLICK_TEXT usa l'OCR di Windows con le coordinate delle parole: veloce (<1s) e preciso per
pulsanti, voci di menu, link con un'etichetta testuale. CLICK_ELEMENT usa il modello di
visione locale per elementi senza testo ("l'icona delle impostazioni", "il pulsante blu"):
piu' lento e meno affidabile, e' il ripiego quando l'OCR non basta.

pyautogui.FAILSAFE resta attivo: portare il mouse in un angolo interrompe tutto."""
import json
import re
from difflib import SequenceMatcher
from core.turn_cancellation import current_turn_cancelled
from core.computer_use.sensitive_ui import EFFECT_PARAMETER, gate_sensitive_ui_action
from core.skill_result import SkillResult


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9àèéìòù]+", " ", (text or "").lower()).strip()


def find_text_on_screen(target: str, words: list[dict]) -> dict | None:
    """Cerca la sequenza di parole 'target' tra le parole OCR (stessa riga, contigue).
    Restituisce il centro del rettangolo che le racchiude, o None."""
    target_words = _normalize(target).split()
    if not target_words or not words:
        return None
    by_line: dict[int, list[dict]] = {}
    for word in words:
        by_line.setdefault(word["line"], []).append(word)

    best, best_score = None, 0.0
    for line_words in by_line.values():
        texts = [_normalize(w["text"]) for w in line_words]
        for start in range(len(line_words)):
            end = start + len(target_words)
            if end > len(line_words):
                break
            candidate = " ".join(texts[start:end])
            score = SequenceMatcher(None, candidate, " ".join(target_words)).ratio()
            if candidate == " ".join(target_words):
                score = 1.0
            if score > best_score:
                span = line_words[start:end]
                x1 = min(w["x"] for w in span)
                y1 = min(w["y"] for w in span)
                x2 = max(w["x"] + w["w"] for w in span)
                y2 = max(w["y"] + w["h"] for w in span)
                best = {"x": (x1 + x2) // 2, "y": (y1 + y2) // 2, "matched": " ".join(w["text"] for w in span), "score": score}
                best_score = score
    return best if best is not None and best_score >= 0.8 else None


class ClickTextSkill:
    metadata = {
        "intent": "CLICK_TEXT",
        "description": "Clicca su un pulsante, link o voce di menu che ha una scritta, cercandola sullo "
        "schermo. Usalo per 'clicca su Accedi', 'premi il pulsante Salva', 'clicca dove c'e' scritto OK'. "
        "Diverso da PRESS_KEY (tasti della tastiera) e da CLICK_MOUSE (coordinate numeriche).",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "La scritta da cliccare, cosi' come appare (es. 'Accedi', 'Salva con nome')."},
            "button": {"type": "string", "required": False, "description": "'left' (default), 'right' o 'double'."},
            "effect": EFFECT_PARAMETER,
        },
    }
    policy_engine = None  # iniettato da JakeCore: serve alle azioni dichiarate sensibili

    def __init__(self, computer_agent=None):
        from core.computer_agent import ComputerAgent

        self.computer_agent = computer_agent or ComputerAgent()

    def execute(self, parameters: dict = None):
        if current_turn_cancelled():
            return SkillResult(
                success=False,
                data={},
                error="CANCELLED",
            )
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        button = (parameters.get("button") or "left").strip().lower()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        gated = gate_sensitive_ui_action(parameters, self.policy_engine, text)
        if gated is not None:
            return gated

        try:
            words = self.computer_agent.observe()
        except Exception:
            words = None
        if words is None:
            return SkillResult(success=False, data={"text": text}, error="OCR_UNAVAILABLE")

        hit = self.computer_agent.locate_text(text, words=words)
        if hit is None:
            return SkillResult(success=False, data={"text": text}, error="NOT_FOUND")

        result = self.computer_agent.click_point(hit["x"], hit["y"], button, matched=hit["matched"])
        if not result.success:
            return SkillResult(success=False, data={"text": text}, error=result.error)
        return SkillResult(success=True, data={
            "text": result.matched, "x": result.x, "y": result.y,
            "screen_changed": result.verified, "change_ratio": result.change_ratio,
        })


class ClickElementSkill:
    metadata = {
        "intent": "CLICK_ELEMENT",
        "description": "Clicca su un elemento dello schermo descritto a parole, senza una scritta "
        "precisa (es. 'l'icona delle impostazioni', 'il pulsante blu in basso', 'la x del popup'), "
        "usando il modello di visione. Se l'elemento ha una scritta leggibile preferisci CLICK_TEXT.",
        "parameters": {
            "description": {"type": "string", "required": True, "description": "Descrizione dell'elemento da cliccare."},
            "effect": EFFECT_PARAMETER,
        },
    }
    policy_engine = None  # iniettato da JakeCore: serve alle azioni dichiarate sensibili

    MAX_WIDTH = 1280

    def __init__(self, vision_provider, computer_agent=None):
        from core.computer_agent import ComputerAgent

        self.vision_provider = vision_provider
        self.computer_agent = computer_agent or ComputerAgent()

    def execute(self, parameters: dict = None):
        from core.vision.screen import capture_screenshot_image, SCREENSHOTS_DIR

        parameters = parameters or {}
        description = (parameters.get("description") or "").strip()
        if not description:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        gated = gate_sensitive_ui_action(parameters, self.policy_engine, description)
        if gated is not None:
            return gated

        # Primo tentativo economico: la descrizione contiene una scritta presente sullo schermo?
        try:
            hit = self.computer_agent.locate_text(description)
        except Exception:
            hit = None
        if hit is not None and hit["score"] >= 0.9:
            return self._click(hit["x"], hit["y"], description)

        image = capture_screenshot_image()
        width, height = image.size
        scale = 1.0
        if width > self.MAX_WIDTH:
            scale = self.MAX_WIDTH / width
            image = image.resize((self.MAX_WIDTH, int(height * scale)))
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOTS_DIR / "click_element_probe.png"
        image.save(path)

        question = (
            f"L'immagine e' uno screenshot di {image.size[0]}x{image.size[1]} pixel. Trova questo elemento: "
            f"\"{description}\". Rispondi SOLO con JSON: {{\"found\": true, \"x\": <pixel>, \"y\": <pixel>}} "
            f"con il centro dell'elemento in pixel dell'immagine, oppure {{\"found\": false}} se non c'e'."
        )
        answer = self.vision_provider.describe(path, question=question)
        if current_turn_cancelled():
            return SkillResult(
                success=False,
                data={"description": description},
                error="CANCELLED",
            )
        if not answer:
            return SkillResult(success=False, data={"description": description}, error="VISION_UNAVAILABLE")
        match = re.search(r"\{.*\}", answer, flags=re.DOTALL)
        try:
            payload = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            payload = {}
        if not payload.get("found") or not isinstance(payload.get("x"), (int, float)) or not isinstance(payload.get("y"), (int, float)):
            return SkillResult(success=False, data={"description": description}, error="NOT_FOUND")
        x = int(payload["x"] / scale)
        y = int(payload["y"] / scale)
        if not (0 <= x <= width and 0 <= y <= height):
            return SkillResult(success=False, data={"description": description}, error="NOT_FOUND")
        return self._click(x, y, description)

    def _click(self, x: int, y: int, description: str):
        if current_turn_cancelled():
            return SkillResult(
                success=False,
                data={"description": description},
                error="CANCELLED",
            )

        result = self.computer_agent.click_point(
            x,
            y,
            matched=description,
        )

        if not result.success:
            return SkillResult(
                success=False,
                data={"description": description},
                error=result.error,
            )

        return SkillResult(
            success=True,
            data={
                "description": description,
                "x": x,
                "y": y,
                "screen_changed": result.verified,
                "change_ratio": result.change_ratio,
            },
        )


class ScrollSkill:
    metadata = {
        "intent": "SCROLL",
        "description": "Scorre la pagina/finestra attiva verso il basso o verso l'alto. Usalo per "
        "'scorri giu'', 'vai su', 'vai in fondo alla pagina', 'torna in alto'.",
        "parameters": {
            "direction": {"type": "string", "required": True, "description": "'down' oppure 'up'."},
            "amount": {"type": "integer", "required": False, "description": "Quanto scorrere (default 8; 50 o piu' = fino in fondo/in cima)."},
        },
    }

    def execute(self, parameters: dict = None):
        import pyautogui

        parameters = parameters or {}
        direction = (parameters.get("direction") or "down").strip().lower()
        amount = parameters.get("amount") or 8
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            amount = 8
        if direction not in ("up", "down"):
            direction = "down"
        try:
            if amount >= 50:
                pyautogui.press("end" if direction == "down" else "home")
            else:
                pyautogui.scroll(-amount if direction == "down" else amount)
        except Exception:
            return SkillResult(success=False, data={"direction": direction}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"direction": direction, "amount": amount})
