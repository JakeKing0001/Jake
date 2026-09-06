from datetime import date, datetime

from core.skill_result import SkillResult


class CalculateBmiSkill:
    metadata = {
        "intent": "CALCULATE_BMI",
        "description": "Calcola l'indice di massa corporea (BMI) dato peso e altezza.",
        "parameters": {
            "weight_kg": {"type": "number", "required": True, "description": "Peso in chilogrammi."},
            "height_cm": {"type": "number", "required": True, "description": "Altezza in centimetri."},
        },
    }

    CATEGORIES = [
        (18.5, "sottopeso"), (25, "normopeso"), (30, "sovrappeso"), (float("inf"), "obesita'"),
    ]

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        weight = parameters.get("weight_kg")
        height = parameters.get("height_cm")
        if not isinstance(weight, (int, float)) or not isinstance(height, (int, float)) or height <= 0:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        height_m = height / 100
        bmi = round(weight / (height_m ** 2), 1)
        category = next(label for threshold, label in self.CATEGORIES if bmi < threshold)
        return SkillResult(success=True, data={"bmi": bmi, "category": category})


class CalculateTipSkill:
    metadata = {
        "intent": "CALCULATE_TIP",
        "description": "Calcola la mancia da lasciare e l'eventuale divisione del conto tra piu' persone.",
        "parameters": {
            "amount": {"type": "number", "required": True, "description": "Importo totale del conto."},
            "percent": {"type": "number", "required": False, "description": "Percentuale di mancia (default 10)."},
            "people": {"type": "integer", "required": False, "description": "Tra quante persone dividere (default 1)."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        amount = parameters.get("amount")
        if not isinstance(amount, (int, float)):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        percent = parameters.get("percent") if isinstance(parameters.get("percent"), (int, float)) else 10
        people = parameters.get("people") if isinstance(parameters.get("people"), int) and parameters.get("people") > 0 else 1

        tip = round(amount * percent / 100, 2)
        total = round(amount + tip, 2)
        per_person = round(total / people, 2)

        return SkillResult(success=True, data={"tip": tip, "total": total, "per_person": per_person, "people": people})


class CalculateAgeSkill:
    metadata = {
        "intent": "CALCULATE_AGE",
        "description": "Calcola l'eta' attuale a partire da una data di nascita.",
        "parameters": {
            "birth_date": {"type": "string", "required": True, "description": "Data di nascita in formato 'GG/MM/AAAA'."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_date = (parameters.get("birth_date") or "").strip()
        if not raw_date:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            birth = datetime.strptime(raw_date, "%d/%m/%Y").date()
        except ValueError:
            return SkillResult(success=False, data={"birth_date": raw_date}, error="INVALID_DATE")

        today = date.today()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        return SkillResult(success=True, data={"age": age})


class CalculateDiscountSkill:
    metadata = {
        "intent": "CALCULATE_DISCOUNT",
        "description": "Calcola il prezzo finale e il risparmio dato un prezzo originale e una percentuale di sconto.",
        "parameters": {
            "price": {"type": "number", "required": True, "description": "Prezzo originale."},
            "discount_percent": {"type": "number", "required": True, "description": "Percentuale di sconto."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        price = parameters.get("price")
        discount_percent = parameters.get("discount_percent")
        if not isinstance(price, (int, float)) or not isinstance(discount_percent, (int, float)):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        savings = round(price * discount_percent / 100, 2)
        final_price = round(price - savings, 2)
        return SkillResult(success=True, data={"final_price": final_price, "savings": savings})
