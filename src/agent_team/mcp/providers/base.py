"""Abstract base class for MCP type-specific providers."""
import re


class MCPProvider:
    """Base class for MCP server type providers.

    Each provider encapsulates type-specific knowledge:
    - How to detect this server type from tool schemas
    - Relationship discovery queries (e.g., FK queries for databases)
    - Extraction patterns for actionable content in agent output
    - Content cleaning rules
    """

    name: str = ""
    detect_params: list[str] = []  # Tool param names that identify this type

    def get_relationship_queries(self) -> list[str]:
        """Return queries that discover relationships between resources.

        For databases: FK constraint queries.
        For other types: override as needed.
        """
        return []

    def get_extract_patterns(self) -> dict[str, list[tuple[str, int]]]:
        """Return extraction patterns specific to this provider type.

        Keys are pattern names (e.g., 'sql', 'path'), values are lists
        of (regex, flags) tuples.
        """
        return {}

    def clean_extracted(self, content: str, pattern_key: str) -> str | None:
        """Clean extracted content. Return None to reject.

        Override for type-specific cleaning (e.g., strip SQL comments).
        """
        return content.strip()

    def find_query_param(self, tool) -> str | None:
        """Find the parameter name that accepts actionable input.

        Scans tool.input_schema for params matching detect_params.
        """
        props = tool.input_schema.get("properties", {})
        for p in props:
            if p.lower() in self.detect_params:
                return p
        return None
