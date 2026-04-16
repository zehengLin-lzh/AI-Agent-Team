"""Web search MCP provider — search query extraction, result cleaning."""
from __future__ import annotations

import re

from agent_team.mcp.providers.base import MCPProvider


class WebSearchProvider(MCPProvider):
    """Provider for web search MCP servers (Tavily, etc.).

    Detection is by tool *name* rather than input-schema params, because
    tools like ``tavily-extract`` accept a ``urls`` param that would
    collide with the APIProvider's ``detect_params``.
    """

    name = "websearch"
    detect_params: list[str] = []  # Disabled — detection is by tool name

    tool_name_patterns: list[str] = [
        # Actual Tavily MCP server uses underscores
        "tavily_search", "tavily_extract", "tavily_crawl",
        "tavily_map", "tavily_research",
        # Also match hyphenated variants (other MCP servers may use them)
        "tavily-search", "tavily-extract", "tavily-crawl",
        # Generic web search names
        "web_search", "search_web",
    ]

    _SEARCH_PATTERNS: list[tuple[str, int]] = [
        # ```search ... ``` blocks
        (r"```search\s*\n(.*?)```", re.DOTALL | re.IGNORECASE),
        # query="..." or query='...'
        (r"query\s*=\s*[\"'](.*?)[\"']", re.DOTALL | re.IGNORECASE),
        # search_query="..." or search_query='...'
        (r"search_query\s*=\s*[\"'](.*?)[\"']", re.DOTALL | re.IGNORECASE),
        # Inline backtick: `search for ...`
        (r"`search\s+(?:for\s+)?([^`]+)`", re.IGNORECASE),
    ]

    def get_extract_patterns(self) -> dict[str, list[tuple[str, int]]]:
        return {"search_query": list(self._SEARCH_PATTERNS)}

    def clean_extracted(self, content: str, pattern_key: str) -> str | None:
        """Reject empty or very short search strings."""
        cleaned = content.strip()
        if not cleaned or len(cleaned) < 3:
            return None
        return cleaned
