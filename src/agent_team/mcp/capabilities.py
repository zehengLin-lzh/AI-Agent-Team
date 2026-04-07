"""MCP tool capability categorization and output extraction.

Categorizes tools into roles (discovery, inspection, action) via auto-detection
or explicit mcp.json config.  Provides extraction patterns to pull actionable
content (SQL, file paths, URLs, commands) from agent output for auto-execution.
"""
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_team.mcp.client import MCPTool


# ── Role detection keywords ──────────────────────────────────────────────────
# Matched against tool name + description (lowercased).
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


# ── Extraction patterns ──────────────────────────────────────────────────────
# Maps pattern name → list of (regex, flags) to extract content from agent
# output.  Each regex should capture the actionable content in group 1.

EXTRACT_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "sql": [
        # ```sql ... ```  blocks (may contain -- comments)
        (r"```sql\s*\n(.*?)```", re.DOTALL | re.IGNORECASE),
        # sql="..." or sql='...' inside code
        (r"sql\s*=\s*[\"'](.*?)[\"']", re.DOTALL | re.IGNORECASE),
        # Bare SELECT ... until ; or blank line
        (r"(SELECT\s+.+?)(?:;|\n\n|```|\Z)", re.DOTALL | re.IGNORECASE),
        # Inline backtick: `SELECT ...`
        (r"`(SELECT\s+[^`]+)`", re.IGNORECASE),
    ],
    "path": [
        (r"```(?:path|file)\s*\n(.*?)```", re.DOTALL),
        (r"(?:file|path)\s*[:=]\s*[`\"']([^`\"']+)[`\"']", 0),
    ],
    "url": [
        (r"```(?:url|endpoint)\s*\n(.*?)```", re.DOTALL),
        (r"(?:url|endpoint)\s*[:=]\s*[`\"']([^`\"']+)[`\"']", 0),
        (r"(https?://[^\s<>\"{}|\\^`\[\]]+)", 0),
    ],
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


# ── Standard FK queries for known database engines ───────────────────────────
# Tried in order; first successful result wins.

_STANDARD_FK_QUERIES: list[str] = [
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


def detect_relationship_queries(tools: list["MCPTool"]) -> list[str]:
    """Auto-detect if any tool accepts a SQL/query param.

    If yes, return standard FK queries for SQLite/MySQL/PostgreSQL.
    If no, return empty list (column-name heuristic will be used instead).
    """
    for tool in tools:
        props = tool.input_schema.get("properties", {})
        if any(p.lower() in ("sql", "query") for p in props):
            return list(_STANDARD_FK_QUERIES)
    return []


def find_query_param(tool: "MCPTool") -> str | None:
    """Find the parameter name that accepts a SQL/query string."""
    props = tool.input_schema.get("properties", {})
    for p in props:
        if p.lower() in ("sql", "query"):
            return p
    return None


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


def categorize_tools(
    server_name: str,
    tools: list["MCPTool"],
    explicit_config: dict | None = None,
) -> MCPCapabilities:
    """Categorize tools into discovery / inspection / action roles.

    Args:
        server_name: MCP server identifier.
        tools: All tools exposed by the server.
        explicit_config: Optional ``capabilities`` dict from mcp.json.
            Keys: "discovery", "inspection", "action" (lists of tool names),
                  "extract_patterns" (list of pattern keys like "sql").
            When provided, overrides auto-detection.
    """
    if explicit_config:
        return _from_explicit(server_name, tools, explicit_config)
    return _from_auto(server_name, tools)


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


def extract_content(text: str, pattern_key: str) -> list[str]:
    """Extract actionable content from text using the named pattern.

    Returns cleaned, non-empty strings that match the pattern.
    """
    regexes = EXTRACT_PATTERNS.get(pattern_key, [])
    if not regexes:
        return []
    matches: list[str] = []
    for regex, flags in regexes:
        matches.extend(re.findall(regex, text, flags))
        if matches:
            break  # Use the first pattern that matches

    # Clean results
    cleaned: list[str] = []
    for m in matches:
        # Strip comment lines (-- ...) for SQL
        if pattern_key == "sql":
            lines = [ln for ln in m.splitlines()
                     if not ln.strip().startswith("--")]
            m = "\n".join(lines)
        m = m.strip().rstrip(";").strip()
        if not m:
            continue
        # For SQL, verify it starts with a statement keyword
        if pattern_key == "sql":
            if not re.match(r"^(?:SELECT|INSERT|UPDATE|DELETE|WITH)\s",
                            m, re.IGNORECASE):
                continue
        cleaned.append(m)
    return cleaned


# ── Internal helpers ─────────────────────────────────────────────────────────

def _from_explicit(
    server_name: str,
    tools: list["MCPTool"],
    config: dict,
) -> MCPCapabilities:
    """Build capabilities from explicit mcp.json config."""
    tool_map = {t.name: t for t in tools}
    discovery = [tool_map[n] for n in config.get("discovery", []) if n in tool_map]
    inspection = [tool_map[n] for n in config.get("inspection", []) if n in tool_map]
    action = [tool_map[n] for n in config.get("action", []) if n in tool_map]

    # Tools not mentioned in config → auto-classify
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

    # Extract patterns: explicit or inferred from action tools
    patterns = config.get("extract_patterns", [])
    if not patterns:
        for t in action:
            patterns.extend(infer_extract_patterns(t))
        patterns = list(dict.fromkeys(patterns))  # dedupe, preserve order

    # Relationship queries: explicit or auto-detected
    rel_queries = config.get("relationship_queries", [])
    if not rel_queries:
        rel_queries = detect_relationship_queries(action)

    return MCPCapabilities(server_name, discovery, inspection, action,
                           patterns, rel_queries)


def _from_auto(
    server_name: str,
    tools: list["MCPTool"],
) -> MCPCapabilities:
    """Build capabilities via auto-detection from tool metadata."""
    discovery, inspection, action = [], [], []
    for t in tools:
        role = _classify_tool(t)
        if role == "discovery":
            discovery.append(t)
        elif role == "inspection":
            inspection.append(t)
        else:
            action.append(t)

    # Infer extract patterns from action tools
    patterns: list[str] = []
    for t in action:
        patterns.extend(infer_extract_patterns(t))
    patterns = list(dict.fromkeys(patterns))

    rel_queries = detect_relationship_queries(action)
    return MCPCapabilities(server_name, discovery, inspection, action,
                           patterns, rel_queries)
