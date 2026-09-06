import random

from core.skill_result import SkillResult

QUOTES = [
    "Il modo per iniziare e' smettere di parlare e iniziare a fare. - Walt Disney",
    "Non e' che non riusciamo a vedere la soluzione, e' che non riusciamo a vedere il problema. - G.K. Chesterton",
    "Le cose belle della vita non sono gratis, ma quasi. - detto popolare",
    "La semplicita' e' la massima sofisticazione. - Leonardo da Vinci",
    "Chi ha un perche' per vivere puo' sopportare quasi ogni come. - Friedrich Nietzsche",
    "Fai in modo che ogni giorno conti. - detto popolare",
]

FACTS = [
    "Il miele non scade mai: e' stato ritrovato commestibile in tombe egizie di 3000 anni fa.",
    "I polpi hanno tre cuori e sangue blu.",
    "Un fulmine e' cinque volte piu' caldo della superficie del sole.",
    "Le banane sono bacche, ma le fragole no, botanicamente parlando.",
    "Venere e' l'unico pianeta del sistema solare che ruota in senso orario.",
]

MAGIC_8_BALL_ANSWERS = [
    "Sì, sicuramente.", "E' certo.", "Senza dubbio.", "Le prospettive sono buone.",
    "Rispondi di nuovo piu' tardi.", "Non posso predirlo ora.", "Concentrati e chiedi di nuovo.",
    "Non contarci.", "La mia risposta e' no.", "Le prospettive non sono buone.",
]


class RandomQuoteSkill:
    metadata = {
        "intent": "RANDOM_QUOTE",
        "description": "Restituisce una citazione o frase motivazionale a caso.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        return SkillResult(success=True, data={"quote": random.choice(QUOTES)})


class RandomFactSkill:
    metadata = {
        "intent": "RANDOM_FACT",
        "description": "Restituisce una curiosita' a caso.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        return SkillResult(success=True, data={"fact": random.choice(FACTS)})


class Magic8BallSkill:
    metadata = {
        "intent": "MAGIC_8_BALL",
        "description": "Risponde a una domanda sì/no in modo oracolare, come la classica palla magica 8.",
        "parameters": {
            "question": {"type": "string", "required": False, "description": "La domanda posta, se presente."},
        },
    }

    def execute(self, parameters: dict = None):
        return SkillResult(success=True, data={"answer": random.choice(MAGIC_8_BALL_ANSWERS)})


class ChooseRandomSkill:
    metadata = {
        "intent": "CHOOSE_RANDOM",
        "description": "Sceglie a caso una tra piu' opzioni date dall'utente, per aiutarlo a decidere.",
        "parameters": {
            "options": {
                "type": "array",
                "required": True,
                "description": "Elenco delle opzioni tra cui scegliere.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        options = parameters.get("options") or []
        if not isinstance(options, list) or len(options) < 2:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        return SkillResult(success=True, data={"choice": random.choice(options)})


class RockPaperScissorsSkill:
    metadata = {
        "intent": "ROCK_PAPER_SCISSORS",
        "description": "Gioca a sasso, carta, forbice contro l'utente.",
        "parameters": {
            "choice": {"type": "string", "required": True, "description": "Una tra: 'sasso', 'carta', 'forbice'."},
        },
    }

    BEATS = {"sasso": "forbice", "carta": "sasso", "forbice": "carta"}

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        user_choice = (parameters.get("choice") or "").strip().lower()
        if user_choice not in self.BEATS:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        jake_choice = random.choice(list(self.BEATS))
        if jake_choice == user_choice:
            outcome = "pareggio"
        elif self.BEATS[jake_choice] == user_choice:
            outcome = "vince Jake"
        else:
            outcome = "vince l'utente"

        return SkillResult(success=True, data={"user_choice": user_choice, "jake_choice": jake_choice, "outcome": outcome})
