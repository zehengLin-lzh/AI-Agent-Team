"""MCP registry — manages server connections and tool discovery."""
import asyncio
from dataclasses import dataclass, field

from agent_team.mcp.config import MCPConfig, MCPServerDef
from agent_team.mcp.client import MCPStdioClient, MCPTool, MCPToolResult


@dataclass
class MCPServerStatus:
    """Runtime status of an MCP server."""
    name: str
    type: str
    connected: bool = False
    tools: list[MCPTool] = field(default_factory=list)
    enabled: bool = True
    description: str = ""
    is_remote: bool = False
    error: str = ""


class MCPRegistry:
    """Central registry for all MCP servers and their tools."""

    def __init__(self, config: MCPConfig | None = None):
        self.config = config or MCPConfig()
        self._clients: dict[str, MCPStdioClient] = {}
        self._statuses: dict[str, MCPServerStatus] = {}

    async def connect_server(self, name: str) -> MCPServerStatus:
        """Connect to a single MCP server by name."""
        server_def = self.config.servers.get(name)
        if not server_def:
            return MCPServerStatus(name=name, type="unknown", error="Server not configured")

        if not server_def.enabled:
            return MCPServerStatus(
                name=name, type=server_def.type,
                enabled=False, description=server_def.description,
            )

        if server_def.is_remote:
            status = MCPServerStatus(
                name=name, type="sse", is_remote=True,
                description=server_def.description,
                error="Remote SSE servers require manual tool configuration",
            )
            self._statuses[name] = status
            return status

        # Stdio server — connect
        client = MCPStdioClient(server_def)
        connected = await client.connect()

        status = MCPServerStatus(
            name=name,
            type=server_def.type,
            connected=connected,
            tools=client.get_tools() if connected else [],
            enabled=True,
            description=server_def.description,
        )

        if connected:
            self._clients[name] = client
        else:
            status.error = "Failed to connect"

        self._statuses[name] = status
        return status

    async def connect_all(self) -> list[MCPServerStatus]:
        """Connect to all enabled servers."""
        results = []
        for name, server_def in self.config.servers.items():
            if server_def.enabled:
                status = await self.connect_server(name)
                results.append(status)
        return results

    async def disconnect_server(self, name: str):
        """Disconnect a specific server."""
        client = self._clients.pop(name, None)
        if client:
            await client.disconnect()
        if name in self._statuses:
            self._statuses[name].connected = False
            self._statuses[name].tools = []

    async def disconnect_all(self):
        """Disconnect all servers."""
        for name in list(self._clients.keys()):
            await self.disconnect_server(name)

    def get_all_tools(self) -> list[MCPTool]:
        """Get all available tools across all connected servers."""
        tools = []
        for client in self._clients.values():
            tools.extend(client.get_tools())
        return tools

    def get_tools_for_server(self, name: str) -> list[MCPTool]:
        """Get tools for a specific server."""
        client = self._clients.get(name)
        if client:
            return client.get_tools()
        return []

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> MCPToolResult:
        """Call a tool on a specific server."""
        client = self._clients.get(server_name)
        if not client:
            return MCPToolResult(content=f"Server '{server_name}' not connected", is_error=True)
        return await client.call_tool(tool_name, arguments)

    async def call_tool_by_name(self, tool_name: str, arguments: dict) -> MCPToolResult:
        """Find and call a tool by name (searches all servers)."""
        for server_name, client in self._clients.items():
            for tool in client.get_tools():
                if tool.name == tool_name:
                    return await client.call_tool(tool_name, arguments)
        return MCPToolResult(content=f"Tool '{tool_name}' not found", is_error=True)

    def get_statuses(self) -> list[MCPServerStatus]:
        """Get status of all configured servers."""
        statuses = []
        for name, server_def in self.config.servers.items():
            if name in self._statuses:
                statuses.append(self._statuses[name])
            else:
                statuses.append(MCPServerStatus(
                    name=name, type=server_def.type,
                    enabled=server_def.enabled,
                    description=server_def.description,
                    is_remote=server_def.is_remote,
                ))
        return statuses

    def format_tools_prompt(self) -> str:
        """Format available MCP tools as text for inclusion in agent prompts."""
        tools = self.get_all_tools()
        if not tools:
            return ""

        lines = ["## Available MCP Tools\n"]
        lines.append("You have access to external tools via MCP. To use a tool, output a tool call block:\n")
        lines.append("```")
        lines.append("--- TOOL_CALL: <tool_name> ---")
        lines.append('{"param1": "value1", "param2": "value2"}')
        lines.append("--- END TOOL_CALL ---")
        lines.append("```\n")
        lines.append("## Tool Usage Guidelines\n")
        lines.append("CRITICAL — You MUST follow a TOOL-FIRST approach:")
        lines.append("1. When you need factual information that a tool can provide — USE the tool, do NOT ask the user")
        lines.append("2. Before asking the user ANY question, check: can one of your available tools answer this?")
        lines.append("3. You can make multiple tool calls in one response — chain them for multi-step discovery")
        lines.append("4. Only ask the user (WAITING_FOR_USER) when NO tool can answer: subjective preferences, business decisions, ambiguous intent")
        lines.append("5. After receiving tool results, reason about them and make follow-up tool calls if needed\n")
        lines.append("Pattern: discover → inspect → act → verify")
        lines.append("  Example: list available resources → describe the relevant one → perform the operation → check results\n")
        lines.append("Anti-pattern: asking the user for information a tool can provide")
        lines.append("  Bad: WAITING_FOR_USER: 'What table should I query?' → Use list/describe tools to find out!")
        lines.append("  Bad: WAITING_FOR_USER: 'What's the schema?' → Use discovery tools to inspect it!\n")
        lines.append("Available tools:\n")

        for tool in tools:
            lines.append(f"### `{tool.name}` (server: {tool.server_name})")
            if tool.description:
                lines.append(f"{tool.description}")
            if tool.input_schema.get("properties"):
                lines.append("**Parameters:**")
                props = tool.input_schema["properties"]
                required = tool.input_schema.get("required", [])
                for pname, pdef in props.items():
                    req = " (required)" if pname in required else ""
                    desc = pdef.get("description", pdef.get("type", ""))
                    lines.append(f"- `{pname}`: {desc}{req}")
            lines.append("")

        return "\n".join(lines)

    def get_capabilities(self) -> dict:
        """Get categorized capabilities for all connected servers.

        Returns:
            dict mapping server_name → MCPCapabilities.
        """
        from agent_team.mcp.capabilities import categorize_tools
        result = {}
        for name, client in self._clients.items():
            server_def = self.config.servers.get(name)
            explicit = server_def.capabilities if server_def else None
            result[name] = categorize_tools(name, client.get_tools(), explicit)
        return result

    def find_tools_by_keywords(self, keywords: list[str]) -> list[MCPTool]:
        """Find tools whose name or description matches any of the keywords."""
        matches = []
        kw_lower = [k.lower() for k in keywords]
        for tool in self.get_all_tools():
            text = f"{tool.name} {tool.description}".lower()
            if any(kw in text for kw in kw_lower):
                matches.append(tool)
        return matches
