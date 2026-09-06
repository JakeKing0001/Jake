import math
import random

from core.skill_result import SkillResult


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0:
        return False
    for divisor in range(3, int(math.isqrt(n)) + 1, 2):
        if n % divisor == 0:
            return False
    return True


class IsPrimeSkill:
    metadata = {
        "intent": "IS_PRIME",
        "description": "Verifica se un numero e' primo.",
        "parameters": {
            "number": {"type": "integer", "required": True, "description": "Il numero da verificare."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        number = parameters.get("number")
        if not isinstance(number, int):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        return SkillResult(success=True, data={"number": number, "is_prime": _is_prime(number)})


class FibonacciSkill:
    metadata = {
        "intent": "FIBONACCI",
        "description": "Restituisce l'N-esimo numero della sequenza di Fibonacci.",
        "parameters": {
            "n": {"type": "integer", "required": True, "description": "Posizione nella sequenza (a partire da 0)."},
        },
    }

    MAX_N = 1000

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        n = parameters.get("n")
        if not isinstance(n, int) or n < 0 or n > self.MAX_N:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        previous, current = 0, 1
        for _ in range(n):
            previous, current = current, previous + current
        return SkillResult(success=True, data={"n": n, "result": previous})


class GcdLcmSkill:
    metadata = {
        "intent": "GCD_LCM",
        "description": "Calcola il massimo comun divisore (MCD) e il minimo comune multiplo (mcm) tra due numeri.",
        "parameters": {
            "a": {"type": "integer", "required": True, "description": "Primo numero."},
            "b": {"type": "integer", "required": True, "description": "Secondo numero."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        a = parameters.get("a")
        b = parameters.get("b")
        if not isinstance(a, int) or not isinstance(b, int) or a == 0 or b == 0:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        gcd = math.gcd(a, b)
        lcm = abs(a * b) // gcd
        return SkillResult(success=True, data={"gcd": gcd, "lcm": lcm})


class CalculatePercentageSkill:
    metadata = {
        "intent": "CALCULATE_PERCENTAGE",
        "description": "Calcola una percentuale di un valore (es. 'quanto e' il 20% di 150').",
        "parameters": {
            "percent": {"type": "number", "required": True, "description": "La percentuale, es. 20 per il 20%."},
            "value": {"type": "number", "required": True, "description": "Il valore su cui calcolare la percentuale."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        percent = parameters.get("percent")
        value = parameters.get("value")
        if not isinstance(percent, (int, float)) or not isinstance(value, (int, float)):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        result = round(percent * value / 100, 4)
        return SkillResult(success=True, data={"percent": percent, "value": value, "result": result})


class RandomNumberSkill:
    metadata = {
        "intent": "RANDOM_NUMBER",
        "description": "Genera un numero casuale in un intervallo.",
        "parameters": {
            "min": {"type": "integer", "required": False, "description": "Valore minimo (default 1)."},
            "max": {"type": "integer", "required": False, "description": "Valore massimo (default 100)."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        minimum = parameters.get("min") if isinstance(parameters.get("min"), int) else 1
        maximum = parameters.get("max") if isinstance(parameters.get("max"), int) else 100
        if minimum > maximum:
            minimum, maximum = maximum, minimum

        return SkillResult(success=True, data={"result": random.randint(minimum, maximum)})
