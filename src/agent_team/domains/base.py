"""Base class for domain plugins."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_team.artifacts.types import Artifact


class DomainPlugin(ABC):
    """Abstract domain plugin — provides domain-specific prompts and output handling.

    Each domain defines:
    - How to detect if a task belongs to this domain (detect)
    - What instructions executors/reviewers get (get_executor_prompt, get_reviewer_prompt)
    - How to parse structured output from raw LLM text (parse_output)
    - How to validate the output (validate)
    """

    name: str = ""
    description: str = ""
    triggers: list[str] = []  # Keywords that boost detection score

    @abstractmethod
    def detect(self, request: str) -> float:
        """Score 0.0-1.0 how relevant this domain is to the request."""
        ...

    @abstractmethod
    def get_executor_prompt(self) -> str:
        """Return domain-specific instructions for the executor agent."""
        ...

    @abstractmethod
    def get_reviewer_prompt(self) -> str:
        """Return domain-specific review criteria."""
        ...

    @abstractmethod
    def parse_output(self, raw_output: str) -> list[Artifact]:
        """Extract structured artifacts from raw LLM output."""
        ...

    def validate(self, artifacts: list[Artifact]) -> list[str]:
        """Validate artifacts. Returns list of issues (empty = valid).

        Default: no validation. Override for domain-specific checks.
        """
        return []
