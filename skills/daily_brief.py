"""F6.6 (brief giornaliero) nel runtime: "com'e' la mia giornata?" costruisce il brief di core/daily_brief.py dalle
fonti locali reali (promemoria delle prossime 24 ore, attivita' aperte). Ogni riga viene da una fonte dichiarata;
una fonte che non risponde viene detta, non riempita con dati inventati. Sola lettura."""
import time

from core.daily_brief import BriefBuilder, RemindersSource, TodosSource
from core.skill_result import SkillResult


class DailyBriefSkill:
    metadata = {
        "intent": "DAILY_BRIEF",
        "description": (
            "Riepilogo della giornata: promemoria delle prossime 24 ore e attivita' ancora aperte, ognuno con la sua "
            "fonte. Per 'com'e' la mia giornata', 'cosa ho oggi', 'fammi il punto della giornata'."
        ),
        "parameters": {
            "detailed": {"type": "boolean", "required": False,
                         "description": "true se l'utente chiede i dettagli (fonte ed eta' di ogni dato)."},
        },
    }

    def __init__(self, reminder_manager, todo_manager, clock=time.time):
        self.sources = [RemindersSource(reminder_manager), TodosSource(todo_manager)]
        self.clock = clock

    def execute(self, parameters: dict = None):
        detailed = (parameters or {}).get("detailed") is True
        brief = BriefBuilder(self.sources, clock=self.clock).build("detailed" if detailed else "short")
        return SkillResult(success=True, data={
            "text": brief.text,
            "sections": {name: [r.text for r in items] for name, items in brief.sections.items()},
            "unavailable": [name for name, _ in brief.unavailable],
            "provenance": brief.provenance(),
        })
