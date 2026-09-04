from core.skill_result import SkillResult


class GetBrowserHistorySkill:
    """Legge la cronologia recente di Chrome/Edge, in sola lettura (v3.0)."""

    metadata = {
        "intent": "GET_BROWSER_HISTORY",
        "description": "Elenca i siti visitati piu' di recente nel browser.",
        "parameters": {
            "limit": {
                "type": "integer",
                "required": False,
                "description": "Quanti siti recenti mostrare (default 10).",
            },
        },
    }

    def execute(self, parameters: dict = None):
        from core.browser_history import read_recent_history

        parameters = parameters or {}
        limit = parameters.get("limit") or 10

        entries = read_recent_history(limit=int(limit))
        if entries is None:
            return SkillResult(success=False, data={}, error="BROWSER_HISTORY_UNAVAILABLE")
        if not entries:
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        return SkillResult(success=True, data={"entries": entries})
