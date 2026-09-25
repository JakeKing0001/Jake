from core.computer_use.sensitive_ui import EFFECT_PARAMETER, gate_sensitive_ui_action
from core.skill_result import SkillResult

# Nomi "parlati" o italiani dei tasti -> nomi riconosciuti da pyautogui.
KEY_ALIASES = {
    "invio": "enter", "return": "enter", "spazio": "space", "barra spaziatrice": "space",
    "escape": "esc", "canc": "delete", "cancella": "backspace", "tabulazione": "tab",
    "control": "ctrl", "controllo": "ctrl", "maiusc": "shift", "shift": "shift", "windows": "win",
    "start": "win", "plus": "+", "piu": "+", "più": "+", "minus": "-", "meno": "-",
    "su": "up", "giu": "down", "giù": "down", "sinistra": "left", "destra": "right",
    "freccia su": "up", "freccia giu": "down", "freccia giù": "down", "freccia sinistra": "left", "freccia destra": "right",
    "inizio": "home", "fine": "end", "pagina su": "pageup", "pagina giu": "pagedown", "pagina giù": "pagedown",
    "stamp": "printscreen", "print screen": "printscreen", "punto": ".", "virgola": ",",
}


class TypeTextSkill:
    metadata = {
        "intent": "TYPE_TEXT",
        "description": "Digita del testo nel punto in cui si trova il focus della tastiera (es. un campo di testo aperto).",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Testo da digitare, con le stesse parole dell'utente.",
            },
            "effect": EFFECT_PARAMETER,
        },
    }
    policy_engine = None  # iniettato da JakeCore: serve alle azioni dichiarate sensibili

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = parameters.get("text") or ""
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        gated = gate_sensitive_ui_action(parameters, self.policy_engine, "il campo attivo")
        if gated is not None:
            return gated

        try:
            # keyboard.write gestisce anche accenti e caratteri unicode (pyautogui.write no:
            # su "perché" scriveva "perch").
            import keyboard
            keyboard.write(text, delay=0.005)
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"text": text})


class PressKeySkill:
    metadata = {
        "intent": "PRESS_KEY",
        "description": "Preme un tasto o una combinazione di tasti (es. 'enter', 'ctrl+c', 'alt+tab', 'win+i', 'f5').",
        "parameters": {
            "keys": {
                "type": "string",
                "required": True,
                "description": "Tasto o combinazione separata da '+', es. 'ctrl+s', 'enter', 'ctrl+shift+t', 'win+.'.",
            },
            "effect": EFFECT_PARAMETER,
        },
    }
    policy_engine = None  # iniettato da JakeCore: serve alle azioni dichiarate sensibili

    def execute(self, parameters: dict = None):
        import pyautogui

        parameters = parameters or {}
        keys = (parameters.get("keys") or "").strip().lower()
        if not keys:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        gated = gate_sensitive_ui_action(parameters, self.policy_engine, keys)
        if gated is not None:
            return gated

        if keys == "+":
            key_sequence = ["+"]
        else:
            key_sequence = [KEY_ALIASES.get(key.strip(), key.strip()) for key in keys.split("+") if key.strip()]
            if keys.endswith("+") and key_sequence:
                key_sequence.append("+")  # es. "ctrl++"
        if not key_sequence:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        try:
            if len(key_sequence) > 1:
                pyautogui.hotkey(*key_sequence)
            else:
                pyautogui.press(key_sequence[0])
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"keys": "+".join(key_sequence)})
