import random

from core.skill_result import SkillResult

JOKES = [
    "Perche' i programmatori confondono Halloween e Natale? Perche' OCT 31 e' uguale a DEC 25.",
    "Un byte entra in un bar e ordina una birra. Il barista chiede: 'Vuoi ghiaccio?' Il byte risponde: 'No grazie, sono gia' abbastanza freddo di mio'.",
    "Ci sono 10 tipi di persone al mondo: quelle che capiscono il binario e quelle che non lo capiscono.",
    "Perche' il computer e' andato dal dottore? Aveva un virus.",
    "Sai qual e' l'animale preferito dai programmatori? Il Python.",
    "Un uomo entra in un negozio e chiede: 'Avete pile?' Il commesso risponde: 'Certo, che tipo?' L'uomo: 'Ricaricabili, per favore, ma quando puo'.",
    "Perche' gli sviluppatori preferiscono il buio? Perche' la luce attira i bug.",
]


class TellJokeSkill:
    metadata = {
        "intent": "TELL_JOKE",
        "description": "Racconta una barzelletta a caso. Usalo per richieste come 'raccontami una "
        "barzelletta', 'dimmi qualcosa di divertente', 'fammi ridere'.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        return SkillResult(success=True, data={"joke": random.choice(JOKES)})


class RollDiceSkill:
    metadata = {
        "intent": "ROLL_DICE",
        "description": "Tira uno o piu' dadi e restituisce il risultato.",
        "parameters": {
            "sides": {
                "type": "integer",
                "required": False,
                "description": "Numero di facce del dado (default 6).",
            },
            "count": {
                "type": "integer",
                "required": False,
                "description": "Quanti dadi tirare insieme (default 1).",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        sides = parameters.get("sides") or 6
        count = parameters.get("count") or 1
        sides = max(2, min(1000, int(sides)))
        count = max(1, min(20, int(count)))

        rolls = [random.randint(1, sides) for _ in range(count)]
        return SkillResult(success=True, data={"sides": sides, "rolls": rolls, "total": sum(rolls)})


class FlipCoinSkill:
    metadata = {
        "intent": "FLIP_COIN",
        "description": "Lancia una moneta virtuale (testa o croce).",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        return SkillResult(success=True, data={"result": random.choice(["testa", "croce"])})
