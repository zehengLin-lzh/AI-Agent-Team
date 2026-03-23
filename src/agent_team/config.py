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
    "ORCHESTRATOR": FAST_MODEL,
    "THINKER": REASONING_MODEL,
    "CHALLENGER": REASONING_MODEL,
    "THINKER_REFINED": REASONING_MODEL,
    "PLANNER": REASONING_MODEL,
    "EXECUTOR": CODING_MODEL,
    "REVIEWER": REASONING_MODEL,
    "chat": FAST_MODEL,
    "ask": FAST_MODEL,
}

# ── Scan security ───────────────────────────────────────────────────────────
SENSITIVE_FILE_PATTERNS = [".env", "credentials", "secret", "service_account", "private_key"]
SENSITIVE_EXTENSIONS = [".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"]
SENSITIVE_CONTENT_RE = r"(?i)(api[_-]?key|secret[_-]?key|password|token|auth[_-]?token|private[_-]?key)\s*[=:]\s*\S+"
MAX_FIX_LOOPS = 3
PLAN_DIR_ENV = "AGENT_TEAM_PLAN_DIR"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # src/agent_team/ → repo root
CONFIG_FILE = REPO_ROOT / "agent_team.config.json"
DEFAULT_PLAN_DIR = REPO_ROOT / "plans"
DATA_DIR = REPO_ROOT / "data"
MEMORY_DB_PATH = DATA_DIR / "memory.db"
SESSIONS_DIR = DATA_DIR / "sessions"
MAX_CONTEXT_TOKENS = 24000
MAX_INPUT_LENGTH = 50000
