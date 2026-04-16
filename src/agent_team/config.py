"""Agent Team configuration."""
import json
import os
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"

# ── Provider-aware model defaults ──────────────────────────────────────────
# Each provider has different model name formats. Store both and select at runtime.

PROVIDER_MODELS = {
    "ollama": {"fast": "qwen2.5-coder:7b", "reasoning": "qwen3:14b"},
    "openrouter": {"fast": "qwen/qwen3-coder:free", "reasoning": "google/gemma-4-31b-it:free"},
}

def _detect_provider() -> str:
    """Quick provider detection for config-time defaults."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    try:
        from pathlib import Path as _P
        env_file = _P(__file__).resolve().parent.parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY=") and line.split("=", 1)[1].strip():
                    return "openrouter"
    except Exception:
        pass
    return "ollama"

_PROVIDER = _detect_provider()
_models = PROVIDER_MODELS.get(_PROVIDER, PROVIDER_MODELS["ollama"])
MODEL = _models["fast"]
THINKING_MODEL = _models["reasoning"]

# ── Three-tier model routing ────────────────────────────────────────────────
FAST_MODEL = MODEL                  # Chat/ask/orchestrator — lightweight conversation
REASONING_MODEL = THINKING_MODEL    # Thinker/planner/reviewer — logical analysis
CODING_MODEL = THINKING_MODEL        # Executor — use reasoning model for stronger code output

MODEL_ROUTING: dict[str, str | None] = {
    # Legacy agents
    "ORCHESTRATOR": FAST_MODEL,
    "THINKER": REASONING_MODEL,
    "CHALLENGER": REASONING_MODEL,
    "THINKER_REFINED": REASONING_MODEL,
    "PLANNER": REASONING_MODEL,
    "EXECUTOR": CODING_MODEL,
    "REVIEWER": REASONING_MODEL,
    "chat": FAST_MODEL,
    "ask": FAST_MODEL,
    # Named agents (12-agent pipeline)
    "ORCH_LUMUSI": FAST_MODEL,
    "ORCH_IVOR": FAST_MODEL,
    "THINK_SOREN": REASONING_MODEL,
    "THINK_MIKA": REASONING_MODEL,
    "THINK_VERA": REASONING_MODEL,
    "PLAN_ATLAS": REASONING_MODEL,
    "PLAN_NORA": REASONING_MODEL,
    "EXEC_KAI": CODING_MODEL,
    "EXEC_DEV": CODING_MODEL,
    "EXEC_SAGE": CODING_MODEL,
    "REV_QUINN": REASONING_MODEL,
    "REV_LENA": REASONING_MODEL,
}

# ── Scan security ───────────────────────────────────────────────────────────
SENSITIVE_FILE_PATTERNS = [".env", "credentials", "secret", "service_account", "private_key"]
SENSITIVE_EXTENSIONS = [".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"]
SENSITIVE_CONTENT_RE = r"(?i)(api[_-]?key|secret[_-]?key|password|token|auth[_-]?token|private[_-]?key)\s*[=:]\s*\S+"
# ── Simple-task model routing (all agents use fast model) ──────────────────
SIMPLE_MODEL_ROUTING: dict[str, str] = {
    "ORCHESTRATOR": FAST_MODEL,
    "PLANNER": FAST_MODEL,
    "EXECUTOR": FAST_MODEL,
    "REVIEWER": FAST_MODEL,
}

# ── Medium-task model routing (secondary agents use fast model) ────────────
MEDIUM_MODEL_ROUTING: dict[str, str] = {
    "ORCH_LUMUSI": FAST_MODEL,
    "ORCH_IVOR": FAST_MODEL,
    "THINK_SOREN": REASONING_MODEL,
    "PLAN_ATLAS": REASONING_MODEL,
    "EXEC_KAI": CODING_MODEL,
    "REV_QUINN": REASONING_MODEL,
    "REV_LENA": FAST_MODEL,   # lighter review for medium tasks
}

# ── Task graph routing (Phase 2) ───────────────────────────────────────────
USE_TASK_GRAPH = True   # Use TaskGraph executor; False falls back to legacy loop
DYNAMIC_ROUTING = False  # Use LLM-assisted routing (requires USE_TASK_GRAPH)

MAX_FIX_LOOPS = 3
MAX_TOOL_ROUNDS = 3  # Max tool-call → see-result → reason iterations per agent
MAX_SUBAGENTS_PER_AGENT = 1
SUBAGENT_MAX_INPUT_TOKENS = 2000
SUBAGENT_MAX_OUTPUT_TOKENS = 1000
DISCUSSION_MAX_OUTPUT_TOKENS = 2000
PLAN_DIR_ENV = "AGENT_TEAM_PLAN_DIR"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # src/agent_team/ → repo root
CONFIG_FILE = REPO_ROOT / "agent_team.config.json"
DEFAULT_PLAN_DIR = REPO_ROOT / "plans"
DATA_DIR = REPO_ROOT / "data"
MEMORY_DB_PATH = DATA_DIR / "memory.db"
SESSIONS_DIR = DATA_DIR / "sessions"
MAX_CONTEXT_TOKENS = 24000
MAX_INPUT_LENGTH = 50000
OLLAMA_NUM_CTX = 16384  # Ollama context window — must be set explicitly
                        # 32768 causes timeouts on 14B+ models; 16384 is safe for all

# --- Web search settings ---
WEB_SEARCH_SUMMARIZE: bool = True
WEB_SEARCH_TOP_K: int = 5
WEB_SEARCH_PER_BODY_CHARS: int = 600
WEB_SEARCH_TOTAL_BYTES: int = 3000
