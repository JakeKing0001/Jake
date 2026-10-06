import ast
import math
import operator
import re

from core.skill_result import SkillResult

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


# Funzioni ammesse, per nome: niente altri nomi, attributi o chiamate. Prova reale del 07/10/2026: "radice quadrata di
# 400" diventava 'sqrt(400)' e la risposta era "non e' un'espressione valida".
_ALLOWED_FUNCTIONS = {"sqrt": math.sqrt, "abs": abs}
MAX_EXPONENT = 1000          # 2**100000000 bloccherebbe Jake per minuti
MAX_EXPRESSION_CHARS = 200


def _safe_eval(node):
    """Valuta solo espressioni aritmetiche pure (numeri, operatori e le poche funzioni di _ALLOWED_FUNCTIONS): niente
    altri nomi, attributi o chiamate, cosi' non si trasforma in un eval() arbitrario su input vocale."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValueError("Esponente troppo grande")
        return _ALLOWED_OPERATORS[type(node.op)](left, right)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCTIONS
            and len(node.args) == 1 and not node.keywords):
        return _ALLOWED_FUNCTIONS[node.func.id](_safe_eval(node.args[0]))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("Espressione non valida")


class CalculateSkill:
    metadata = {
        "intent": "CALCULATE",
        "description": (
            "Calcola un'espressione matematica e restituisce il risultato. Converti la richiesta "
            "dell'utente in una normale espressione aritmetica (es. 'quanto fa 5 per 3 piu' 2' -> "
            "'5*3+2', 'la radice quadrata di 16' -> 'sqrt(16)', '2 alla decima' -> '2**10'). Se l'utente vuole "
            "vederlo SULLA CALCOLATRICE usa SHOW_ON_CALCULATOR."
        ),
        "parameters": {
            "expression": {
                "type": "string",
                "required": True,
                "description": "Espressione aritmetica: + - * / ** ( ), sqrt(x), abs(x).",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        expression = normalize_expression(parameters.get("expression") or "")
        try:
            result = evaluate(expression)
        except ValueError:
            return SkillResult(success=False, data={"expression": expression}, error="INVALID_EXPRESSION")
        return SkillResult(success=True, data={"expression": expression, "result": result})


_VALID_CHARS = re.compile(r"^[\d\.\+\-\*/%\(\)\sa-z]+$")


def normalize_expression(expression: str) -> str:
    """Virgola decimale, ^ per le potenze, simbolo √ e "radice" scritti dal modello -> la forma che evaluate() accetta."""
    expression = expression.strip().lower().replace(",", ".").replace("^", "**").replace("×", "*").replace("÷", "/")
    expression = re.sub(r"√\s*\(", "sqrt(", expression)
    expression = re.sub(r"√\s*([\d.]+)", r"sqrt(\1)", expression)
    return re.sub(r"\bradice(?:\s+quadrata)?\s*\(", "sqrt(", expression)


def evaluate(expression: str):
    """Il valore dell'espressione (int se intero); ValueError se non e' un'espressione ammessa o non calcolabile."""
    if not expression or len(expression) > MAX_EXPRESSION_CHARS or not _VALID_CHARS.match(expression):
        raise ValueError("Espressione non valida")
    try:
        result = _safe_eval(ast.parse(expression, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError, OverflowError) as exc:
        raise ValueError("Espressione non valida") from exc
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        return int(result)
    return result
