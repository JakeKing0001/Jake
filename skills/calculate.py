import ast
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


def _safe_eval(node):
    """Valuta solo espressioni aritmetiche pure (numeri e operatori): niente nomi, chiamate di
    funzione o attributi, cosi' non si trasforma in un eval() arbitrario su input vocale."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("Espressione non valida")


class CalculateSkill:
    metadata = {
        "intent": "CALCULATE",
        "description": (
            "Calcola un'espressione matematica e restituisce il risultato. Converti la richiesta "
            "dell'utente in una normale espressione aritmetica (es. 'quanto fa 5 per 3 piu' 2' -> "
            "'5*3+2', 'la radice quadrata di 16' non e' supportata, ma le quattro operazioni e le "
            "potenze si', es. '2**10')."
        ),
        "parameters": {
            "expression": {
                "type": "string",
                "required": True,
                "description": "Espressione aritmetica in notazione standard: + - * / ** ( ).",
            },
        },
    }

    _VALID_CHARS = re.compile(r"^[\d\.\+\-\*/%\(\)\s]+$")

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        expression = (parameters.get("expression") or "").strip().replace(",", ".").replace("^", "**")
        if not expression or not self._VALID_CHARS.match(expression):
            return SkillResult(success=False, data={"expression": expression}, error="INVALID_EXPRESSION")

        try:
            tree = ast.parse(expression, mode="eval")
            result = _safe_eval(tree)
        except (SyntaxError, ValueError, ZeroDivisionError, TypeError, OverflowError):
            return SkillResult(success=False, data={"expression": expression}, error="INVALID_EXPRESSION")

        return SkillResult(success=True, data={"expression": expression, "result": result})
