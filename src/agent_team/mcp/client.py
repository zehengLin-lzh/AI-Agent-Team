"""MCP client — communicates with MCP servers via stdio (JSON-RPC 2.0)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agent_team.mcp.config import MCPServerDef


# Matches ${VAR_NAME} for environment variable expansion
_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_env_vars(env_dict: dict[str, str]) -> dict[str, str]:
    """Expand ``${VAR_NAME}`` references in *env_dict* values from ``os.environ``."""
    expanded: dict[str, str] = {}
    for key, value in env_dict.items():
        expanded[key] = _ENV_VAR_RE.sub(
            lambda m: os.environ.get(m.group(1), ""),
            value,
        )
    return expanded


@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPToolResult:
    """Result from calling an MCP tool."""
    content: str = ""
    is_error: bool = False


class MCPStdioClient:
    """Client that communicates with MCP servers over stdin/stdout (JSON-RPC 2.0)."""

    def __init__(self, server_def: MCPServerDef):
        self.server_def = server_def
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._initialized = False
        self._tools: list[MCPTool] = []

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def connect(self) -> bool:
        """Start the MCP server subprocess and initialize."""
        if self.is_connected:
            return True

        if self.server_def.type != "stdio":
            return False

        # Skip Tavily server when no API key is available
        if (
            self.server_def.name == "tavily"
            or "TAVILY_API_KEY" in self.server_def.env
        ):
            from agent_team.mcp.tavily_config import has_web_search

            if not has_web_search():
                return False

        try:
            env = os.environ.copy()
            env.update(_expand_env_vars(self.server_def.env))

            self._process = await asyncio.create_subprocess_exec(
                self.server_def.command,
                *self.server_def.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            # Send initialize request
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mat-agent-team",
                    "version": "7.0.0",
                },
            })

            if init_result is None:
                await self.disconnect()
                return False

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})
            self._initialized = True

            # Discover tools
            await self._discover_tools()
            return True

        except (FileNotFoundError, PermissionError, OSError) as e:
            self._process = None
            return False

    async def disconnect(self):
        """Shut down the MCP server subprocess."""
        if self._process:
            try:
                self._process.stdin.close() if self._process.stdin else None
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._process.kill()
            except Exception:
                pass
            finally:
                self._process = None
                self._initialized = False
                self._tools = []

    async def _send_request(self, method: str, params: dict) -> dict | None:
        """Send a JSON-RPC request and wait for response."""
        if not self._process or not self._process.stdin or not self._process.stdout:
            return None

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        try:
            msg = json.dumps(request) + "\n"
            self._process.stdin.write(msg.encode())
            await self._process.stdin.drain()

            # Read response (read lines until we get our response)
            while True:
                line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=30.0,
                )
                if not line:
                    return None

                line = line.decode().strip()
                if not line:
                    continue

                try:
                    response = json.loads(line)
                    if response.get("id") == self._request_id:
                        if "error" in response:
                            return None
                        return response.get("result", {})
                except json.JSONDecodeError:
                    continue

        except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
            return None

    async def _send_notification(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        try:
            msg = json.dumps(notification) + "\n"
            self._process.stdin.write(msg.encode())
            await self._process.stdin.drain()
        except Exception:
            pass

    async def _discover_tools(self):
        """Fetch available tools from the server."""
        result = await self._send_request("tools/list", {})
        if result and "tools" in result:
            self._tools = []
            for tool_data in result["tools"]:
                self._tools.append(MCPTool(
                    name=tool_data.get("name", ""),
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                    server_name=self.server_def.name,
                ))

    def get_tools(self) -> list[MCPTool]:
        """Get the list of available tools."""
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict) -> MCPToolResult:
        """Call a tool on the MCP server."""
        if not self._initialized:
            return MCPToolResult(content="MCP server not initialized", is_error=True)

        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })

        if result is None:
            return MCPToolResult(content=f"Tool call failed: {tool_name}", is_error=True)

        # Parse content blocks
        content_parts = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                content_parts.append(block.get("text", ""))
            elif block.get("type") == "resource":
                res = block.get("resource", {})
                content_parts.append(f"[Resource: {res.get('uri', '')}]\n{res.get('text', '')}")

        return MCPToolResult(
            content="\n".join(content_parts) if content_parts else str(result),
            is_error=result.get("isError", False),
        )

    async def list_resources(self) -> list[dict]:
        """List available resources from the server."""
        result = await self._send_request("resources/list", {})
        if result and "resources" in result:
            return result["resources"]
        return []
