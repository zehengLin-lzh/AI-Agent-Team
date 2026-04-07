"""MCP server configuration — load/save from mcp.json."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class MCPServerDef:
    """Definition of an MCP server."""
    name: str
    type: Literal["stdio", "sse"] = "stdio"
    command: str = ""           # For stdio: the command to run
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""               # For SSE: the remote URL
    description: str = ""
    triggers: list[str] = field(default_factory=list)  # Keywords that suggest this server
    enabled: bool = True
    capabilities: dict | None = None  # Optional explicit tool role mapping

    @property
    def is_remote(self) -> bool:
        return self.type == "sse"


class MCPConfig:
    """Load and save MCP server configuration from mcp.json."""

    def __init__(self, config_path: Path | None = None):
        from agent_team.config import REPO_ROOT
        self.config_path = config_path or (REPO_ROOT / "mcp.json")
        self.servers: dict[str, MCPServerDef] = {}
        self.load()

    def load(self):
        """Load configuration from disk."""
        self.servers.clear()
        if not self.config_path.exists():
            return

        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            for name, sdef in data.get("mcpServers", {}).items():
                self.servers[name] = MCPServerDef(
                    name=name,
                    type=sdef.get("type", "stdio"),
                    command=sdef.get("command", ""),
                    args=sdef.get("args", []),
                    env=sdef.get("env", {}),
                    url=sdef.get("url", ""),
                    description=sdef.get("description", ""),
                    triggers=sdef.get("triggers", []),
                    enabled=sdef.get("enabled", True),
                    capabilities=sdef.get("capabilities"),
                )
        except (json.JSONDecodeError, Exception):
            pass

    def save(self):
        """Persist configuration to disk."""
        data = {"mcpServers": {}}
        for name, server in self.servers.items():
            entry: dict = {
                "type": server.type,
                "description": server.description,
                "triggers": server.triggers,
                "enabled": server.enabled,
            }
            if server.type == "stdio":
                entry["command"] = server.command
                entry["args"] = server.args
                if server.env:
                    entry["env"] = server.env
            else:
                entry["url"] = server.url

            data["mcpServers"][name] = entry

        self.config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def add_server(self, server: MCPServerDef):
        """Add or update a server definition."""
        self.servers[server.name] = server
        self.save()

    def remove_server(self, name: str) -> bool:
        """Remove a server. Returns True if found."""
        if name in self.servers:
            del self.servers[name]
            self.save()
            return True
        return False

    def toggle_server(self, name: str) -> bool | None:
        """Toggle a server's enabled status. Returns new state or None if not found."""
        if name not in self.servers:
            return None
        self.servers[name].enabled = not self.servers[name].enabled
        self.save()
        return self.servers[name].enabled

    def list_servers(self) -> list[MCPServerDef]:
        """List all configured servers."""
        return list(self.servers.values())
