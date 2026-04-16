"""Task complexity classification and domain detection for adaptive pipeline routing."""
import re
from dataclasses import dataclass, field
from enum import Enum


class TaskComplexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


@dataclass
class TaskClassification:
    """Rich classification result with complexity, domain, and metadata."""
    complexity: TaskComplexity
    domain: str = "general"      # coding, writing, research, data, general
    key_entities: list[str] = field(default_factory=list)
    needs_tools: bool = False    # whether MCP tools are likely needed
    mode_hint: str = ""          # suggested AgentMode if detectable


# Signals that suggest a simple, single-focus task
_SIMPLE_KEYWORDS = re.compile(
    r"\b(fix|rename|add field|add column|extract|display|show|list|print|"
    r"update value|change value|set|toggle|typo|simple|single file|one file|"
    r"config|env|variable|endpoint|route|hello world)\b",
    re.IGNORECASE,
)

# Signals that suggest a complex, multi-component task
_COMPLEX_KEYWORDS = re.compile(
    r"\b(refactor|redesign|migrate|architecture|multi.?service|integrate|"
    r"pipeline|distributed|microservice|full.?stack|end.?to.?end|"
    r"authentication system|database schema|CI/?CD|deployment)\b",
    re.IGNORECASE,
)

# Multi-step language indicating chained requirements
_MULTI_STEP = re.compile(
    r"\b(then|after that|also need|and also|additionally|furthermore|"
    r"next step|on top of|as well as|followed by)\b",
    re.IGNORECASE,
)

# Multiple technology/component mentions suggest non-trivial integration
_COMPONENT_KEYWORDS = re.compile(
    r"\b(database|sqlite|postgres|mysql|redis|api|fastapi|flask|django|"
    r"websocket|auth|session|cache|queue|docker|kubernetes|frontend|backend|"
    r"llm|model|embedding|vector|streaming)\b",
    re.IGNORECASE,
)

# Pattern to count distinct file references
_FILE_REFS = re.compile(
    r"(?:\b\w+\.(?:py|js|ts|jsx|tsx|java|go|rs|rb|yaml|yml|json|toml|cfg|ini|sql)\b|"
    r"--- FILE:|/[\w/]+\.[\w]+)",
    re.IGNORECASE,
)


def classify_complexity(user_plan: str, mode: str = "coding") -> TaskComplexity:
    """Classify task complexity using heuristics. No LLM call — must be instant.

    Returns SIMPLE for focused single-file tasks, COMPLEX for multi-component
    architecture work, and MEDIUM for everything in between.
    """
    word_count = len(user_plan.split())
    simple_hits = len(_SIMPLE_KEYWORDS.findall(user_plan))
    complex_hits = len(_COMPLEX_KEYWORDS.findall(user_plan))
    multi_step_hits = len(_MULTI_STEP.findall(user_plan))
    file_refs = len(set(_FILE_REFS.findall(user_plan)))
    component_count = len(set(w.lower() for w in _COMPONENT_KEYWORDS.findall(user_plan)))

    # Thinking and brainstorming modes are inherently simpler pipelines
    if mode in ("thinking", "brainstorming"):
        if word_count < 300 and complex_hits == 0:
            return TaskComplexity.SIMPLE
        return TaskComplexity.MEDIUM

    # Strong COMPLEX signals
    if complex_hits >= 2:
        return TaskComplexity.COMPLEX
    if word_count > 500 and file_refs >= 3:
        return TaskComplexity.COMPLEX
    if file_refs >= 5:
        return TaskComplexity.COMPLEX

    # Multiple components = at least medium complexity
    if component_count >= 3:
        return TaskComplexity.MEDIUM

    # Strong SIMPLE signals — only truly trivial one-liners (e.g. "fix typo in X")
    if word_count < 20 and simple_hits >= 2 and multi_step_hits == 0 and file_refs <= 1:
        return TaskComplexity.SIMPLE
    if word_count < 10 and simple_hits >= 1 and complex_hits == 0 and multi_step_hits == 0 and component_count <= 1:
        return TaskComplexity.SIMPLE

    # Default
    return TaskComplexity.MEDIUM


# ── Domain detection keywords ──────────────────────────────────────────────

_CODING_KW = re.compile(
    r"\b(code|implement|function|class|bug|fix|refactor|test|api|endpoint|"
    r"import|library|package|deploy|compile|build|debug|git|commit|"
    r"python|javascript|typescript|rust|go|java|html|css|sql|"
    r"server|client|frontend|backend|fullstack|database|schema)\b",
    re.IGNORECASE,
)

_WRITING_KW = re.compile(
    r"\b(write|draft|compose|essay|report|article|blog|email|letter|"
    r"document|summary|summarize|memo|proposal|outline|paragraph|"
    r"copy|content|proofread|edit text|rewrite|tone|narrative)\b",
    re.IGNORECASE,
)

_RESEARCH_KW = re.compile(
    r"\b(research|find|search|compare|alternatives|pros and cons|"
    r"investigate|analyze data|market analysis|competitive|benchmark|"
    r"survey|literature|papers|sources|citations|evidence|study)\b",
    re.IGNORECASE,
)

_DATA_KW = re.compile(
    r"\b(query|select|insert|update|delete|table|column|row|csv|excel|"
    r"spreadsheet|dashboard|chart|graph|visualization|statistics|"
    r"aggregate|average|count|sum|join|group by|order by|"
    r"patient|record|transaction|inventory|sales|revenue)\b",
    re.IGNORECASE,
)

_TOOL_KW = re.compile(
    r"\b(database|db|sql|file|filesystem|web search|search the web|"
    r"look up|browse|fetch|api call|http|curl|download)\b",
    re.IGNORECASE,
)


def classify_task(user_plan: str, mode: str = "coding") -> TaskClassification:
    """Enhanced classification returning complexity + domain + metadata.

    No LLM call — uses heuristics for instant classification.
    Backward-compatible: complexity result matches classify_complexity().
    """
    complexity = classify_complexity(user_plan, mode)

    # Domain scoring
    coding_hits = len(_CODING_KW.findall(user_plan))
    writing_hits = len(_WRITING_KW.findall(user_plan))
    research_hits = len(_RESEARCH_KW.findall(user_plan))
    data_hits = len(_DATA_KW.findall(user_plan))

    scores = {
        "coding": coding_hits,
        "writing": writing_hits,
        "research": research_hits,
        "data": data_hits,
    }

    # If a mode is explicitly specified, heavily weight that domain
    mode_domain_map = {
        "coding": "coding",
        "execution": "coding",
        "architecture": "coding",
        "thinking": "general",
        "brainstorming": "general",
    }
    if mode in mode_domain_map and mode_domain_map[mode] in scores:
        scores[mode_domain_map[mode]] += 10

    # Pick highest scoring domain, default to "general"
    best_domain = max(scores, key=scores.get)
    if scores[best_domain] == 0:
        best_domain = "general"

    # Tool detection
    needs_tools = bool(_TOOL_KW.search(user_plan))

    # Extract key entities (simple: unique capitalized words and file refs)
    entities = list(set(_FILE_REFS.findall(user_plan)))[:10]

    # Mode hint
    mode_hint = ""
    if best_domain == "coding":
        mode_hint = "coding"
    elif best_domain == "writing":
        mode_hint = "thinking"
    elif best_domain == "research":
        mode_hint = "thinking"
    elif best_domain == "data":
        mode_hint = "execution"

    return TaskClassification(
        complexity=complexity,
        domain=best_domain,
        key_entities=entities,
        needs_tools=needs_tools,
        mode_hint=mode_hint,
    )
