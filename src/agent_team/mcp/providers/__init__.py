"""MCP provider registry — auto-detects server type from tool schemas."""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent_team.mcp.providers.base import MCPProvider
from agent_team.mcp.providers.database import DatabaseProvider
from agent_team.mcp.providers.filesystem import FilesystemProvider
from agent_team.mcp.providers.api import APIProvider

if TYPE_CHECKING:
    from agent_team.mcp.client import MCPTool

# All registered providers (order matters — first match wins)
_PROVIDERS: list[MCPProvider] = [
    DatabaseProvider(),
    FilesystemProvider(),
    APIProvider(),
]


def detect_provider(tools: list[MCPTool]) -> MCPProvider | None:
    """Find the matching provider by scanning tool input schemas.

    Checks each tool's input_schema properties against each provider's
    detect_params.  Returns the first matching provider, or None.
    """
    for tool in tools:
        props = tool.input_schema.get("properties", {})
        param_names = {p.lower() for p in props}
        for provider in _PROVIDERS:
            if param_names & set(provider.detect_params):
                return provider
    return None


def get_provider_by_name(name: str) -> MCPProvider | None:
    """Look up a provider by its name (e.g., 'database', 'filesystem')."""
    for provider in _PROVIDERS:
        if provider.name == name:
            return provider
    return None
