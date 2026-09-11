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

    DEFAULT_LIMIT = 10

    @classmethod
    def _parse_limit(cls, raw) -> int:
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return cls.DEFAULT_LIMIT
        return limit if limit > 0 else cls.DEFAULT_LIMIT

    def execute(self, parameters: dict = None):
        from core.browser_history import read_recent_history

        parameters = parameters or {}
        limit = self._parse_limit(parameters.get("limit"))

        entries = read_recent_history(limit=limit)
        if entries is None:
            return SkillResult(success=False, data={}, error="BROWSER_HISTORY_UNAVAILABLE")
        if not entries:
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        return SkillResult(success=True, data={"entries": entries})
