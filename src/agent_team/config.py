"""Agent Team configuration."""
import json
import os
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_BASE_URL = "http://localhost:11434"
MODEL = "qwen2.5-coder:7b"
EMBEDDING_MODEL = "nomic-embed-text"
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
