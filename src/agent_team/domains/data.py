"""Data domain plugin — SQL, data analysis, queries, spreadsheets."""
from __future__ import annotations

import re
from agent_team.domains.base import DomainPlugin
from agent_team.artifacts.types import Artifact, ArtifactType


_DATA_KW = re.compile(
    r"\b(query|select|insert|update|delete|table|column|row|csv|excel|"
    r"spreadsheet|dashboard|chart|graph|visualization|statistics|"
    r"aggregate|average|count|sum|join|group by|order by|"
    r"patient|record|transaction|inventory|sales|revenue|"
    r"sql|database|db|report data|data analysis)\b",
    re.IGNORECASE,
)

_SQL_BLOCK = re.compile(
    r"```sql\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


class DataPlugin(DomainPlugin):
    name = "data"
    description = "SQL queries, data analysis, spreadsheets, and database operations"
    triggers = ["query", "sql", "table", "database", "data", "csv", "chart"]

    def detect(self, request: str) -> float:
        hits = len(_DATA_KW.findall(request))
        if hits >= 3:
            return 0.9
        if hits >= 1:
            return 0.45
        return 0.05

    def get_executor_prompt(self) -> str:
        return """Produce data queries and analysis. Rules:
- Write correct, optimized SQL queries
- Use proper JOINs and WHERE clauses
- Include comments explaining complex queries
- For analysis: present findings with context, not just raw numbers
- If MCP database tools are available, USE them to validate queries and show results
- Use --- TOOL_CALL: tool_name --- blocks to execute queries via MCP

Output SQL in ```sql code blocks.
Present analysis results in markdown tables."""

    def get_reviewer_prompt(self) -> str:
        return """Review the data work for:
- SQL correctness: proper syntax, JOINs, WHERE clauses
- Performance: avoid N+1 queries, unnecessary full table scans
- Security: no SQL injection risks, parameterized queries
- Data integrity: proper handling of NULLs, edge cases
- Results accuracy: do the numbers make sense?

If issues found: FIX_REQUIRED: followed by specific feedback."""

    def parse_output(self, raw_output: str) -> list[Artifact]:
        artifacts = []
        # Extract SQL blocks
        for i, m in enumerate(_SQL_BLOCK.finditer(raw_output)):
            sql = m.group(1).strip()
            if sql:
                artifacts.append(Artifact(
                    type=ArtifactType.QUERY,
                    content=sql,
                    title=f"Query {i+1}",
                    language="sql",
                ))
        # The full output is also an analysis artifact
        if raw_output.strip():
            artifacts.append(Artifact(
                type=ArtifactType.ANALYSIS,
                content=raw_output.strip(),
                title="Data Analysis",
                format="markdown",
            ))
        return artifacts
