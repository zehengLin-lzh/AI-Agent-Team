"""Filesystem MCP provider — path extraction, file helpers."""
import re
from agent_team.mcp.providers.base import MCPProvider


class FilesystemProvider(MCPProvider):
    """Provider for filesystem MCP servers (local files, S3, etc.)."""

    name = "filesystem"
    detect_params = ["path", "file", "filepath", "file_path", "directory"]

    def get_extract_patterns(self) -> dict[str, list[tuple[str, int]]]:
        return {
            "path": [
                (r"```(?:path|file)\s*\n(.*?)```", re.DOTALL),
                (r"(?:file|path)\s*[:=]\s*[`\"']([^`\"']+)[`\"']", 0),
            ],
        }
