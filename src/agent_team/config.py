"""Agent Team configuration."""
import json
import os
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "qwen2.5-coder:7b"
THINKING_MODEL = "qwen3:14b"  # Used for THINKER, CHALLENGER, debate phases
EMBEDDING_MODEL = "nomic-embed-text"

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
