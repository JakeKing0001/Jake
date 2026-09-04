from dataclasses import dataclass, field


@dataclass
class PlanStep:
    intent: str
    parameters: dict
    description: str = ""


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
