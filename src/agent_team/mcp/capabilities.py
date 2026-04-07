"""MCP tool capability categorization and output extraction.

Categorizes tools into roles (discovery, inspection, action) via auto-detection
or explicit mcp.json config.  Type-specific knowledge (SQL patterns, FK queries,
path extraction) is delegated to providers in ``mcp.providers``.
"""
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_team.mcp.client import MCPTool
    from agent_team.mcp.providers.base import MCPProvider


# ── Role detection keywords ──────────────────────────────────────────────────
_DISCOVERY_KW = {"list", "search", "find", "browse", "enumerate", "scan", "index"}
_INSPECTION_KW = {"describe", "inspect", "detail", "get", "show", "info",
                  "read", "view", "fetch", "metadata"}


def _classify_tool(tool: "MCPTool") -> str:
    """Return 'discovery', 'inspection', or 'action' for a single tool."""
    text = f"{tool.name} {tool.description}".lower()
    words = set(re.split(r"[_\s\-/]+", text))
    if words & _DISCOVERY_KW:
        return "discovery"
    if words & _INSPECTION_KW:
        return "inspection"
    return "action"


# ── Fallback extraction patterns (generic, no type-specific knowledge) ──────
# Provider-supplied patterns are merged on top of these.

_BASE_EXTRACT_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "command": [
        (r"```(?:bash|sh|shell)\s*\n(.*?)```", re.DOTALL),
    ],
}

# Maps tool input-schema parameter names → extraction pattern key.
_PARAM_TO_PATTERN: dict[str, str] = {
    "sql": "sql", "query": "sql",
    "path": "path", "file": "path", "filepath": "path", "file_path": "path",
    "url": "url", "endpoint": "url", "uri": "url",
    "command": "command", "cmd": "command", "shell": "command",
}


# ── Public API ───────────────────────────────────────────────────────────────

@dataclass
class MCPCapabilities:
    """Categorized capabilities of an MCP server."""
    server_name: str
    discovery_tools: list["MCPTool"] = field(default_factory=list)
    inspection_tools: list["MCPTool"] = field(default_factory=list)
    action_tools: list["MCPTool"] = field(default_factory=list)
    extract_patterns: list[str] = field(default_factory=list)
    relationship_queries: list[str] = field(default_factory=list)
    provider: "MCPProvider | None" = None


def categorize_tools(
    server_name: str,
    tools: list["MCPTool"],
    explicit_config: dict | None = None,
) -> MCPCapabilities:
    """Categorize tools into discovery / inspection / action roles.

    Detects the server type via ``providers.detect_provider()`` and
    delegates type-specific knowledge (FK queries, extraction patterns)
    to the matched provider.

    Args:
        server_name: MCP server identifier.
        tools: All tools exposed by the server.
        explicit_config: Optional ``capabilities`` dict from mcp.json.
    """
    from agent_team.mcp.providers import detect_provider

    # Classify tools into roles
    if explicit_config:
        discovery, inspection, action = _split_explicit(tools, explicit_config)
    else:
        discovery, inspection, action = _split_auto(tools)

    # Detect provider (auto or from config)
    provider = detect_provider(tools)

    # Build extract patterns: config > provider > inferred > base
    patterns = _resolve_extract_patterns(explicit_config, provider, action)

    # Build relationship queries: config > provider
    rel_queries = _resolve_relationship_queries(explicit_config, provider)

    return MCPCapabilities(
        server_name, discovery, inspection, action,
        patterns, rel_queries, provider,
    )


def infer_extract_patterns(tool: "MCPTool") -> list[str]:
    """Guess extraction patterns from a tool's input_schema properties."""
    props = tool.input_schema.get("properties", {})
    seen: set[str] = set()
    patterns: list[str] = []
    for param_name in props:
        key = _PARAM_TO_PATTERN.get(param_name.lower())
        if key and key not in seen:
            patterns.append(key)
            seen.add(key)
    return patterns


def extract_content(
    text: str,
    pattern_key: str,
    provider: "MCPProvider | None" = None,
) -> list[str]:
    """Extract actionable content from text using the named pattern.

    Merges provider-specific patterns with base patterns.
    Delegates cleaning to the provider if available.
    """
    # Build pattern list: provider patterns first, then base
    regexes: list[tuple[str, int]] = []
    if provider:
        prov_patterns = provider.get_extract_patterns()
        regexes.extend(prov_patterns.get(pattern_key, []))
    regexes.extend(_BASE_EXTRACT_PATTERNS.get(pattern_key, []))

    if not regexes:
        return []

    matches: list[str] = []
    for regex, flags in regexes:
        matches.extend(re.findall(regex, text, flags))
        if matches:
            break

    # Clean results via provider or basic strip
    cleaned: list[str] = []
    for m in matches:
        if provider:
            result = provider.clean_extracted(m, pattern_key)
        else:
            result = m.strip()
        if result:
            cleaned.append(result)
    return cleaned


# ── Internal helpers ─────────────────────────────────────────────────────────

def _split_explicit(
    tools: list["MCPTool"], config: dict,
) -> tuple[list, list, list]:
    """Split tools using explicit config, auto-classify unlisted ones."""
    tool_map = {t.name: t for t in tools}
    discovery = [tool_map[n] for n in config.get("discovery", []) if n in tool_map]
    inspection = [tool_map[n] for n in config.get("inspection", []) if n in tool_map]
    action = [tool_map[n] for n in config.get("action", []) if n in tool_map]

    mentioned = {t.name for t in discovery + inspection + action}
    for t in tools:
        if t.name not in mentioned:
            role = _classify_tool(t)
            if role == "discovery":
                discovery.append(t)
            elif role == "inspection":
                inspection.append(t)
            else:
                action.append(t)
    return discovery, inspection, action


def _split_auto(tools: list["MCPTool"]) -> tuple[list, list, list]:
    """Split tools via auto-detection from metadata."""
    discovery, inspection, action = [], [], []
    for t in tools:
        role = _classify_tool(t)
        if role == "discovery":
            discovery.append(t)
        elif role == "inspection":
            inspection.append(t)
        else:
            action.append(t)
    return discovery, inspection, action


def _resolve_extract_patterns(
    config: dict | None,
    provider: "MCPProvider | None",
    action_tools: list["MCPTool"],
) -> list[str]:
    """Resolve extraction pattern keys from config, provider, or inference."""
    # 1. Explicit config
    if config and config.get("extract_patterns"):
        return config["extract_patterns"]

    patterns: list[str] = []

    # 2. Provider patterns
    if provider:
        patterns.extend(provider.get_extract_patterns().keys())

    # 3. Inferred from tool schemas
    for t in action_tools:
        patterns.extend(infer_extract_patterns(t))

    return list(dict.fromkeys(patterns))  # dedupe, preserve order


def _resolve_relationship_queries(
    config: dict | None,
    provider: "MCPProvider | None",
) -> list[str]:
    """Resolve relationship queries from config or provider."""
    if config and config.get("relationship_queries"):
        return config["relationship_queries"]
    if provider:
        return provider.get_relationship_queries()
    return []
