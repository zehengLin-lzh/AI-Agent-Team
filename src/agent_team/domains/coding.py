"""Coding domain plugin — wraps existing file extraction and code review logic."""
from __future__ import annotations

import re
from agent_team.domains.base import DomainPlugin
from agent_team.artifacts.types import Artifact, ArtifactType


_CODING_KW = re.compile(
    r"\b(code|implement|function|class|bug|fix|refactor|test|api|endpoint|"
    r"import|library|package|deploy|compile|build|debug|git|commit|"
    r"python|javascript|typescript|rust|go|java|html|css|"
    r"server|client|frontend|backend|fullstack|database|schema)\b",
    re.IGNORECASE,
)

_FILE_BLOCK = re.compile(
    r"---\s*FILE:\s*(.+?)\s*---\s*\n(.*?)---\s*END\s*FILE\s*---",
    re.DOTALL,
)


class CodingPlugin(DomainPlugin):
    name = "coding"
    description = "Code generation, implementation, and software engineering tasks"
    triggers = ["code", "implement", "function", "class", "api", "bug", "fix"]

    def detect(self, request: str) -> float:
        hits = len(_CODING_KW.findall(request))
        if hits >= 3:
            return 0.9
        if hits >= 1:
            return 0.5
        return 0.0  # No coding keywords → not coding

    def get_executor_prompt(self) -> str:
        return """Implement exactly what PLANNER specified. Rules:
- Write ONE complete file at a time
- NO stubs, NO placeholders, NO TODOs, NO simulated/mock implementations
- Actually implement ALL functionality
- Include ALL necessary imports at the top of each file

Use this EXACT format for EVERY file you produce:
--- FILE: path/to/file ---
actual code here
--- END FILE ---

IMPORTANT: Do NOT use markdown code blocks (no ```). Only the --- FILE --- format."""

    def get_reviewer_prompt(self) -> str:
        return """Review all code for:
- Logic errors, off-by-one mistakes, missing null checks
- Hardcoded values that should be config
- Performance issues, security vulnerabilities
- Plan compliance (does it match what was requested?)
- MENTAL COMPILATION: verify every import exists, ports are correct, names are consistent

If fixes needed: FIX_REQUIRED: followed by specific issues."""

    def parse_output(self, raw_output: str) -> list[Artifact]:
        """Extract --- FILE --- blocks into code file artifacts."""
        from agent_team.files.writer import _normalize_file_blocks
        normalized = _normalize_file_blocks(raw_output)
        artifacts = []
        for match in _FILE_BLOCK.finditer(normalized):
            path = match.group(1).strip()
            content = match.group(2)
            # Detect language from extension
            ext = path.rsplit(".", 1)[-1] if "." in path else ""
            lang_map = {
                "py": "python", "js": "javascript", "ts": "typescript",
                "jsx": "javascript", "tsx": "typescript", "rs": "rust",
                "go": "go", "java": "java", "rb": "ruby",
                "html": "html", "css": "css", "sql": "sql",
                "yaml": "yaml", "yml": "yaml", "json": "json",
                "toml": "toml", "md": "markdown", "sh": "bash",
            }
            artifacts.append(Artifact(
                type=ArtifactType.CODE_FILE,
                content=content,
                file_path=path,
                language=lang_map.get(ext, ext),
                title=path,
            ))
        return artifacts

    def validate(self, artifacts: list[Artifact]) -> list[str]:
        issues = []
        for a in artifacts:
            if not a.content.strip():
                issues.append(f"Empty file: {a.file_path}")
            if "TODO" in a.content or "pass  #" in a.content:
                issues.append(f"Placeholder found in {a.file_path}")
        return issues
