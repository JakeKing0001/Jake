"""Plugin di esempio (v2.0): dimostra come aggiungere una skill a Jake senza toccare il core.

Basta un file .py qui dentro con una funzione register(registry): viene caricato in automatico
all'avvio. Cancella pure questo file, e' solo un esempio."""
import random

from core.skill_result import SkillResult


class CoinFlipSkill:
    metadata = {
        "intent": "COIN_FLIP",
        "description": "Lancia una moneta virtuale: testa o croce.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        result = random.choice(["testa", "croce"])
        return SkillResult(success=True, data={"result": result})

    def format_result(self, result: SkillResult) -> str:
        """Facoltativo: personalizza il testo di risposta senza toccare core/jake_core.py."""
        return f"È uscito {result.data['result']}!"


def register(registry) -> None:
    registry.register_skill("COIN_FLIP", CoinFlipSkill())
