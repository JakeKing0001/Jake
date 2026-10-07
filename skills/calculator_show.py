"""Mostra un calcolo sulla Calcolatrice di Windows, digitandolo con la tastiera.

Prova reale del 07/10/2026: "fai sulla calcolatrice radice quadrata di 400" finiva in CALCULATE (calcolo interno, e
sqrt non era nemmeno supportata); l'agente poi cliccava sul TESTO "400" e "4" con l'OCR, che sui tasti della
calcolatrice non e' affidabile. La Calcolatrice accetta la tastiera: cifre, + - * /, '@' radice quadrata, F9 cambio
segno, '=' risultato, Esc azzera (il quadrato diventa x*x: 'q' digitato come carattere non arriva come scorciatoia). Qui l'espressione diventa una sequenza di tasti; i tasti partono SOLO se
la finestra in primo piano e' davvero la Calcolatrice (mai testo digitato in un'altra finestra).

La modalita' Standard esegue le operazioni nell'ordine in cui arrivano ("2+3*4" -> 20): la sequenza viene simulata e,
se il risultato non coincide con quello vero, si digita direttamente il risultato e lo si dice."""
from __future__ import annotations

import ast
import subprocess
import time

from core.skill_result import SkillResult
from skills.calculate import evaluate, normalize_expression

WINDOW_TITLES = ("calcolatrice", "calculator")
_BINARY_KEYS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


def _number_keys(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def expression_keys(expression: str) -> list[str] | None:
    """Tasti (in ordine) per l'espressione in modalita' Standard, o None se non si sa esprimere con i tasti."""
    try:
        tree = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return None

    def walk(node) -> list[str] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return [_number_keys(node.value)]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
            inner = walk(node.operand)
            return inner + ["f9"] if inner else None
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_KEYS:
            left, right = walk(node.left), walk(node.right)
            return left + [_BINARY_KEYS[type(node.op)]] + right if left and right else None
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant)
                and node.right.value == 2):
            left = walk(node.left)
            # il tasto 'q' (x^2) non arriva come scorciatoia se digitato come carattere: 9*9 e' identico e si vede
            return left + ["*"] + left if left and len(left) == 1 else None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt"
                and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)):
            inner = walk(node.args[0])
            return inner + ["@"] if inner else None
        return None

    keys = walk(tree)
    if keys is None:
        return None
    return keys if keys[-1] == "@" and len(keys) <= 2 else keys + ["="]


def simulate_standard(keys: list[str]) -> float | None:
    """Cio' che mostra la Calcolatrice Standard dopo questi tasti (esecuzione immediata, senza precedenze)."""
    accumulator: float | None = None
    pending: str | None = None
    entry: float | None = None
    for key in keys:
        if key in ("+", "-", "*", "/", "="):
            if entry is not None:
                if accumulator is None or pending is None:
                    accumulator = entry
                else:
                    try:
                        accumulator = {"+": accumulator + entry, "-": accumulator - entry, "*": accumulator * entry,
                                       "/": accumulator / entry}[pending]
                    except ZeroDivisionError:
                        return None
            entry = None
            pending = None if key == "=" else key
        elif key == "@":
            if entry is None or entry < 0:
                return None
            entry = entry ** 0.5
        elif key == "f9":
            if entry is None:
                return None
            entry = -entry
        else:
            entry = float(key)
    return entry if entry is not None else accumulator


def _find_calculator():
    from skills.window_control import _find_window

    for title in WINDOW_TITLES:
        match = _find_window(title)
        if match:
            return match
    return None


def _focus(hwnd) -> bool:
    import win32con
    import win32gui

    try:
        import keyboard

        keyboard.send("alt")  # Windows concede il primo piano solo a chi ha appena ricevuto input
    except Exception:
        pass
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        return False
    time.sleep(0.25)
    title = (win32gui.GetWindowText(win32gui.GetForegroundWindow()) or "").lower()
    return any(name in title for name in WINDOW_TITLES)


def decimal_separator() -> str:
    """Il separatore decimale delle impostazioni di Windows: con l'italiano la Calcolatrice ignora il punto ("2.5*2"
    diventava "25 × 2 = 50", prova reale del 07/10/2026)."""
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(8)
        if ctypes.windll.kernel32.GetLocaleInfoW(0x0400, 0x000E, buffer, 8):  # LOCALE_USER_DEFAULT, LOCALE_SDECIMAL
            return buffer.value or "."
    except (AttributeError, OSError):
        pass
    return "."


def _type_keys(keys: list[str]) -> None:
    import keyboard

    separator = decimal_separator()
    keyboard.send("esc")  # azzera il calcolo precedente
    time.sleep(0.1)
    for key in keys:
        if key == "f9":
            keyboard.send("f9")
        else:
            keyboard.write(key.replace(".", separator), delay=0.02)
        time.sleep(0.04)


class ShowOnCalculatorSkill:
    metadata = {
        "intent": "SHOW_ON_CALCULATOR",
        "description": (
            "Esegue un calcolo SULLA CALCOLATRICE di Windows, aprendola e digitando l'operazione, quando l'utente vuole "
            "vederlo li' ('fai sulla calcolatrice la radice di 400', 'calcola 12 per 8 con la calcolatrice', "
            "'mostramelo sulla calcolatrice'). Per un risultato detto a voce senza calcolatrice usa CALCULATE."
        ),
        "parameters": {
            "expression": {
                "type": "string",
                "required": True,
                "description": "Espressione aritmetica: + - * / ** ( ), sqrt(x). Es. 'sqrt(400)', '12*8'.",
            },
        },
    }

    def __init__(self, launch=None, find=None, focus=None, type_keys=None, wait_s: float = 6.0):
        self._launch = launch or (lambda: subprocess.Popen(["calc.exe"]))
        self._find = find or _find_calculator
        self._focus = focus or _focus
        self._type_keys = type_keys or _type_keys
        self.wait_s = wait_s

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        expression = normalize_expression(parameters.get("expression") or "")
        try:
            result = evaluate(expression)
        except ValueError:
            return SkillResult(success=False, data={"expression": expression}, error="INVALID_EXPRESSION")

        keys = expression_keys(expression)
        exact = keys is not None
        if exact:
            shown = simulate_standard(keys)
            exact = shown is not None and abs(shown - float(result)) < 1e-9 * max(1.0, abs(float(result)))
        if not exact:
            keys = [_number_keys(result)]   # la Calcolatrice mostra comunque il risultato giusto

        match = self._find()
        if match is None:
            try:
                self._launch()
            except OSError:
                return SkillResult(success=False, data={"expression": expression}, error="OPERATION_FAILED")
            deadline = time.monotonic() + self.wait_s
            while match is None and time.monotonic() < deadline:
                time.sleep(0.2)
                match = self._find()
        if match is None:
            return SkillResult(success=False, data={"expression": expression}, error="WINDOW_NOT_FOUND")
        if not self._focus(match[0]):
            # mai tasti in un'altra finestra: se la Calcolatrice non e' davanti ci si ferma
            return SkillResult(success=False, data={"expression": expression}, error="CALCULATOR_NOT_FOCUSED")
        try:
            self._type_keys(keys)
        except Exception:
            return SkillResult(success=False, data={"expression": expression}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"expression": expression, "result": result, "keys": keys,
                                               "exact": exact})
