"""API/Web MCP provider — URL extraction, endpoint helpers."""
import re
from agent_team.mcp.providers.base import MCPProvider


class APIProvider(MCPProvider):
    """Provider for API/Web MCP servers (REST, GraphQL, etc.)."""

    name = "api"
    detect_params = ["url", "endpoint", "uri"]

    def get_extract_patterns(self) -> dict[str, list[tuple[str, int]]]:
        return {
            "url": [
                (r"```(?:url|endpoint)\s*\n(.*?)```", re.DOTALL),
                (r"(?:url|endpoint)\s*[:=]\s*[`\"']([^`\"']+)[`\"']", 0),
                (r"(https?://[^\s<>\"{}|\\^`\[\]]+)", 0),
            ],
        }
