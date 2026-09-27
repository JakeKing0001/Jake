"""F6.7 + F4.5.6: "a cosa stai lavorando?" - una sola risposta su cio' che Jake ha in corso: compiti degli agenti ancora
aperti (TaskMonitorRegistry, che finora nessuno leggeva), processi che sta aspettando, una conferma che aspetta
l'utente, notifiche trattenute. Sola lettura."""
import time

from core.skill_result import SkillResult

_STATUS_IT = {"running": "in corso", "anomaly": "rallentato", "needs_decision": "aspetta una tua decisione"}


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "da meno di un minuto"
    return f"da {minutes} min" if minutes < 60 else f"da {minutes // 60} h"


class StatusOverviewSkill:
    metadata = {
        "intent": "STATUS_OVERVIEW",
        "description": (
            "Dice cosa Jake ha in corso adesso: compiti avviati, processi che sta sorvegliando, conferme che aspettano "
            "l'utente, notifiche trattenute. Per 'a cosa stai lavorando?', 'cosa stai facendo?', 'hai qualcosa in sospeso?'."
        ),
        "parameters": {},
    }

    def __init__(self, core, watcher=None, clock=time.time):
        self.core = core
        self.watcher = watcher
        self.clock = clock

    def execute(self, parameters: dict = None):
        now = self.clock()
        tasks = []
        monitor = getattr(self.core, "task_monitor", None)
        for task in (monitor.active() if monitor is not None else []):
            tasks.append({"label": task.label, "status": task.status.value, "since": _ago(now - task.started_at)})
        watches = []
        if self.watcher is not None:
            with self.watcher._lock:
                watches = [name for pid, name in self.watcher.watching.items() if pid not in self.watcher._cancelled]
        pending = self.core.conversation_state.get_pending_action()
        center = getattr(self.core, "notification_center", None)
        return SkillResult(success=True, data={
            "tasks": tasks, "watches": watches,
            "waiting_for_you": bool(pending) and pending.get("reason") in ("confirmation_required", "auth_required"),
            "pending_notifications": center.pending_count() if center is not None else 0,
        })


def format_overview(data: dict) -> str:
    parts = []
    for task in data.get("tasks") or []:
        parts.append(f"sto lavorando a \"{task['label']}\" ({_STATUS_IT.get(task['status'], task['status'])}, {task['since']})")
    if data.get("watches"):
        parts.append("aspetto che finiscano " + ", ".join(data["watches"]))
    if data.get("waiting_for_you"):
        parts.append("aspetto una tua conferma")
    if data.get("pending_notifications"):
        n = data["pending_notifications"]
        parts.append(f"{n} {'notifica è in attesa' if n == 1 else 'notifiche sono in attesa'}")
    if not parts:
        return "Niente in corso: sono libero."
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."
