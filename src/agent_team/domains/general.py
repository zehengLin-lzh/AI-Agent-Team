"""General domain plugin — catch-all for unclassified tasks."""
from __future__ import annotations

from agent_team.domains.base import DomainPlugin
from agent_team.artifacts.types import Artifact, ArtifactType


class GeneralPlugin(DomainPlugin):
    name = "general"
    description = "General-purpose tasks that don't fit a specific domain"
    triggers = []

    def detect(self, request: str) -> float:
        # Always available as fallback, but low priority
        return 0.1

    def get_executor_prompt(self) -> str:
        return """Produce a clear, well-structured response to the request.
- Be thorough but concise
- Use appropriate formatting (headers, lists, tables) for clarity
- If the task involves creating content, make it complete and polished
- If the task involves analysis, show your reasoning"""

    def get_reviewer_prompt(self) -> str:
        return """Review the output for:
- Completeness: does it fully address the request?
- Accuracy: are claims and information correct?
- Clarity: is it well-organized and easy to understand?
- Quality: is it polished and professional?

If issues found: FIX_REQUIRED: followed by specific feedback."""

    def parse_output(self, raw_output: str) -> list[Artifact]:
        if not raw_output.strip():
            return []
        first_line = next(
            (ln.strip().lstrip("# ") for ln in raw_output.splitlines() if ln.strip()),
            "Response",
        )
        return [Artifact(
            type=ArtifactType.GENERIC,
            content=raw_output.strip(),
            title=first_line[:80],
            format="markdown",
        )]
