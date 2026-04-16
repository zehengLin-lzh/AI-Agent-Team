"""Writing domain plugin — reports, emails, documents, summaries."""
from __future__ import annotations

import re
from agent_team.domains.base import DomainPlugin
from agent_team.artifacts.types import Artifact, ArtifactType


_WRITING_KW = re.compile(
    r"\b(write|draft|compose|essay|report|article|blog|email|letter|"
    r"document|summary|summarize|memo|proposal|outline|paragraph|"
    r"copy|content|proofread|edit text|rewrite|tone|narrative)\b",
    re.IGNORECASE,
)


class WritingPlugin(DomainPlugin):
    name = "writing"
    description = "Document generation, reports, emails, summaries, and creative writing"
    triggers = ["write", "draft", "report", "email", "summary", "document"]

    def detect(self, request: str) -> float:
        hits = len(_WRITING_KW.findall(request))
        if hits >= 3:
            return 0.9
        if hits >= 1:
            return 0.5
        return 0.05

    def get_executor_prompt(self) -> str:
        return """Produce the requested document/text. Rules:
- Write complete, polished content — no outlines or bullet-point drafts unless requested
- Use appropriate tone and structure for the document type
- Include headings, sections, and formatting as needed
- If writing an email: include subject line, greeting, body, sign-off
- If writing a report: include executive summary, sections, conclusion

Output the document as markdown. Use --- DOCUMENT: title --- / --- END DOCUMENT --- delimiters:

--- DOCUMENT: Monthly Status Report ---
# Monthly Status Report
...content...
--- END DOCUMENT ---"""

    def get_reviewer_prompt(self) -> str:
        return """Review the document for:
- Completeness: does it cover all requested topics?
- Tone: is it appropriate for the audience?
- Structure: is it well-organized with clear sections?
- Grammar and clarity
- Accuracy of any claims or data referenced

If issues found: FIX_REQUIRED: followed by specific feedback."""

    def parse_output(self, raw_output: str) -> list[Artifact]:
        artifacts = []
        # Try structured --- DOCUMENT --- blocks first
        for m in re.finditer(
            r"---\s*DOCUMENT:\s*(.+?)\s*---\s*\n(.*?)---\s*END\s*DOCUMENT\s*---",
            raw_output, re.DOTALL,
        ):
            artifacts.append(Artifact(
                type=ArtifactType.DOCUMENT,
                content=m.group(2).strip(),
                title=m.group(1).strip(),
                format="markdown",
            ))
        # Fallback: treat entire output as a single document
        if not artifacts and raw_output.strip():
            first_line = next(
                (ln.strip().lstrip("# ") for ln in raw_output.splitlines() if ln.strip()),
                "Document",
            )
            artifacts.append(Artifact(
                type=ArtifactType.DOCUMENT,
                content=raw_output.strip(),
                title=first_line[:80],
                format="markdown",
            ))
        return artifacts
