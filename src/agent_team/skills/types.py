"""Skill type definitions."""
from dataclasses import dataclass, field


@dataclass
class Skill:
    name: str
    description: str
    mode: str  # thinking, coding, brainstorming, architecture, execution, all
    instructions: str  # the markdown body
    allowed_agents: list[str] = field(default_factory=lambda: ["THINKER", "PLANNER", "EXECUTOR", "REVIEWER"])
    requires: dict = field(default_factory=dict)
