from core.skill_result import SkillResult

# Ogni unita' normalizzata converte da/verso una base comune per categoria (metri, grammi):
# aggiungere una nuova unita' significa solo aggiungere una riga qui, non nuova logica.
_LENGTH_TO_METERS = {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001, "mi": 1609.344, "ft": 0.3048, "yd": 0.9144, "in": 0.0254}
_WEIGHT_TO_GRAMS = {"g": 1, "kg": 1000, "mg": 0.001, "lb": 453.592, "oz": 28.3495}

_UNIT_ALIASES = {
    "metri": "m", "metro": "m", "m": "m",
    "chilometri": "km", "chilometro": "km", "km": "km",
    "centimetri": "cm", "centimetro": "cm", "cm": "cm",
    "millimetri": "mm", "millimetro": "mm", "mm": "mm",
    "miglia": "mi", "miglio": "mi", "mi": "mi", "miles": "mi",
    "piedi": "ft", "piede": "ft", "ft": "ft", "feet": "ft",
    "iarde": "yd", "iarda": "yd", "yd": "yd",
    "pollici": "in", "pollice": "in", "in": "in", "inch": "in",
    "grammi": "g", "grammo": "g", "g": "g",
    "chili": "kg", "chilo": "kg", "chilogrammi": "kg", "kg": "kg",
    "milligrammi": "mg", "mg": "mg",
    "libbre": "lb", "libbra": "lb", "lb": "lb", "pounds": "lb",
    "once": "oz", "oncia": "oz", "oz": "oz",
    "celsius": "c", "gradi celsius": "c", "c": "c",
    "fahrenheit": "f", "gradi fahrenheit": "f", "f": "f",
    "kelvin": "k", "k": "k",
}


def _to_celsius(value: float, unit: str) -> float:
    if unit == "c":
        return value
    if unit == "f":
        return (value - 32) * 5 / 9
    return value - 273.15  # kelvin


def _from_celsius(value: float, unit: str) -> float:
    if unit == "c":
        return value
    if unit == "f":
        return value * 9 / 5 + 32
    return value + 273.15  # kelvin


class ConvertUnitsSkill:
    metadata = {
        "intent": "CONVERT_UNITS",
        "description": "Converte un valore da un'unita' di misura a un'altra: lunghezza, peso o temperatura.",
        "parameters": {
            "value": {
                "type": "number",
                "required": True,
                "description": "Il valore numerico da convertire.",
            },
            "from_unit": {
                "type": "string",
                "required": True,
                "description": "Unita' di partenza, es. 'km', 'miglia', 'kg', 'libbre', 'celsius', 'fahrenheit'.",
            },
            "to_unit": {
                "type": "string",
                "required": True,
                "description": "Unita' di destinazione, stesse categorie di from_unit.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        value = parameters.get("value")
        from_unit = _UNIT_ALIASES.get((parameters.get("from_unit") or "").strip().lower())
        to_unit = _UNIT_ALIASES.get((parameters.get("to_unit") or "").strip().lower())

        if not isinstance(value, (int, float)) or from_unit is None or to_unit is None:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        temperature_units = {"c", "f", "k"}
        if from_unit in temperature_units and to_unit in temperature_units:
            result = _from_celsius(_to_celsius(value, from_unit), to_unit)
        elif from_unit in _LENGTH_TO_METERS and to_unit in _LENGTH_TO_METERS:
            result = value * _LENGTH_TO_METERS[from_unit] / _LENGTH_TO_METERS[to_unit]
        elif from_unit in _WEIGHT_TO_GRAMS and to_unit in _WEIGHT_TO_GRAMS:
            result = value * _WEIGHT_TO_GRAMS[from_unit] / _WEIGHT_TO_GRAMS[to_unit]
        else:
            return SkillResult(success=False, data={}, error="INCOMPATIBLE_UNITS")

        return SkillResult(success=True, data={
            "value": value, "from_unit": from_unit, "to_unit": to_unit, "result": round(result, 4),
        })
