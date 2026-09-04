from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SkillResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None