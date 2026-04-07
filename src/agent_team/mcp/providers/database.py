"""Database MCP provider — FK discovery, SQL extraction, schema helpers."""
import re
from agent_team.mcp.providers.base import MCPProvider


class DatabaseProvider(MCPProvider):
    """Provider for SQL database MCP servers (SQLite, MySQL, PostgreSQL)."""

    name = "database"
    detect_params = ["sql", "query"]

    # Standard FK queries for known database engines.
    # Tried in order; first successful result wins.
    _FK_QUERIES: list[str] = [
        # SQLite
        (
            "SELECT m.name AS tbl, p.[from] AS col, "
            "p.[table] AS ref_tbl, p.[to] AS ref_col "
            "FROM sqlite_master m, pragma_foreign_key_list(m.name) p "
            "WHERE m.type = 'table'"
        ),
        # MySQL
        (
            "SELECT TABLE_NAME AS tbl, COLUMN_NAME AS col, "
            "REFERENCED_TABLE_NAME AS ref_tbl, REFERENCED_COLUMN_NAME AS ref_col "
            "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL"
        ),
        # PostgreSQL
        (
            "SELECT tc.table_name AS tbl, kcu.column_name AS col, "
            "ccu.table_name AS ref_tbl, ccu.column_name AS ref_col "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_name = kcu.constraint_name "
            "AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage ccu "
            "ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY'"
        ),
    ]

    _SQL_PATTERNS: list[tuple[str, int]] = [
        # ```sql ... ``` blocks
        (r"```sql\s*\n(.*?)```", re.DOTALL | re.IGNORECASE),
        # sql="..." or sql='...' in code
        (r"sql\s*=\s*[\"'](.*?)[\"']", re.DOTALL | re.IGNORECASE),
        # Bare SELECT ... until ; or blank line
        (r"(SELECT\s+.+?)(?:;|\n\n|```|\Z)", re.DOTALL | re.IGNORECASE),
        # Inline backtick: `SELECT ...`
        (r"`(SELECT\s+[^`]+)`", re.IGNORECASE),
    ]

    def get_relationship_queries(self) -> list[str]:
        return list(self._FK_QUERIES)

    def get_extract_patterns(self) -> dict[str, list[tuple[str, int]]]:
        return {"sql": list(self._SQL_PATTERNS)}

    def clean_extracted(self, content: str, pattern_key: str) -> str | None:
        if pattern_key != "sql":
            return content.strip()
        # Strip SQL single-line comments
        lines = [ln for ln in content.splitlines()
                 if not ln.strip().startswith("--")]
        cleaned = "\n".join(lines).strip().rstrip(";").strip()
        if not cleaned:
            return None
        # Must start with a SQL statement keyword
        if not re.match(r"^(?:SELECT|INSERT|UPDATE|DELETE|WITH)\s",
                        cleaned, re.IGNORECASE):
            return None
        return cleaned
