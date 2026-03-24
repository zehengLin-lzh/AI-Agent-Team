"""Task complexity classification for adaptive pipeline routing."""
import re
from enum import Enum


class TaskComplexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


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

    # Strong SIMPLE signals
    if word_count < 200 and simple_hits >= 1 and multi_step_hits == 0 and file_refs <= 1:
        return TaskComplexity.SIMPLE
    if word_count < 100 and complex_hits == 0 and multi_step_hits == 0 and component_count <= 1:
        return TaskComplexity.SIMPLE

    # Default
    return TaskComplexity.MEDIUM
