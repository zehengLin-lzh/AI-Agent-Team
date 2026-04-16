"""Research domain plugin — information gathering, analysis, comparisons."""
from __future__ import annotations

import re
from agent_team.domains.base import DomainPlugin
from agent_team.artifacts.types import Artifact, ArtifactType


_RESEARCH_KW = re.compile(
    r"\b(research|find|search|compare|alternatives|pros and cons|"
    r"investigate|analyze|market analysis|competitive|benchmark|"
    r"survey|literature|papers|sources|citations|evidence|study|"
    r"what is|explain|how does|latest|current|trends)\b",
    re.IGNORECASE,
)


class ResearchPlugin(DomainPlugin):
    name = "research"
    description = "Information gathering, analysis, comparisons, and explanations"
    triggers = ["research", "compare", "alternatives", "explain", "what is", "find"]

    def detect(self, request: str) -> float:
        hits = len(_RESEARCH_KW.findall(request))
        # Questions are often research
        if request.strip().endswith("?"):
            hits += 2
        if hits >= 3:
            return 0.85
        if hits >= 1:
            return 0.4
        return 0.05

    def get_executor_prompt(self) -> str:
        return """Produce a thorough research analysis. Rules:
- Structure findings clearly with headers and sections
- Cite sources when using web search results
- Present multiple perspectives when applicable
- Include a summary/conclusion section
- For comparisons: use a structured format (table or pros/cons)
- Distinguish facts from opinions/speculation
- If MCP web search tools are available, USE them to ground your analysis in current data

Output as structured analysis. Do NOT use --- FILE --- blocks.
For comparisons, use markdown tables."""

    def get_reviewer_prompt(self) -> str:
        return """Review the research for:
- Accuracy: are claims supported by evidence?
- Completeness: are all aspects of the question addressed?
- Balance: are multiple perspectives presented?
- Recency: is the information current?
- Source quality: are sources credible?

If issues found: FIX_REQUIRED: followed by specific feedback."""

    def parse_output(self, raw_output: str) -> list[Artifact]:
        # Research output is a single analysis artifact
        first_line = next(
            (ln.strip().lstrip("# ") for ln in raw_output.splitlines() if ln.strip()),
            "Research Analysis",
        )
        return [Artifact(
            type=ArtifactType.ANALYSIS,
            content=raw_output.strip(),
            title=first_line[:80],
            format="markdown",
        )]
