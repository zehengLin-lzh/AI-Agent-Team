"""API key management — store, load, mask keys from a local .env file.

Keys are stored in {REPO_ROOT}/.env and auto-loaded on startup.
Keys are NEVER logged or displayed in full — always masked.
"""
import os
import re
from pathlib import Path

from agent_team.config import REPO_ROOT

ENV_FILE = REPO_ROOT / ".env"

# Provider → env var name mapping
PROVIDER_KEY_NAMES: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "cohere": "COHERE_API_KEY",
    "together": "TOGETHER_API_KEY",
    "huggingface": "HF_TOKEN",
}

# Where to get API keys
PROVIDER_KEY_URLS: dict[str, str] = {
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "google": "https://aistudio.google.com/apikey",
    "mistral": "https://console.mistral.ai/api-keys",
    "groq": "https://console.groq.com/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "cohere": "https://dashboard.cohere.com/api-keys",
    "together": "https://api.together.ai/settings/api-keys",
    "huggingface": "https://huggingface.co/settings/tokens",
}


def mask_key(key: str) -> str:
    """Mask an API key for safe display: show first 4 and last 4 chars."""
    if not key:
        return "(not set)"
    if len(key) <= 10:
        return key[:2] + "*" * (len(key) - 2)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def load_env_file() -> dict[str, str]:
    """Load key=value pairs from the .env file."""
    env_vars: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env_vars
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
        if match:
            key_name = match.group(1)
            value = match.group(2).strip().strip('"').strip("'")
            env_vars[key_name] = value
    return env_vars


def load_keys_into_env():
    """Load all keys from .env file into os.environ (if not already set)."""
    env_vars = load_env_file()
    for key_name, value in env_vars.items():
        if key_name not in os.environ:
            os.environ[key_name] = value


def save_key(provider: str, key_value: str) -> None:
    """Save an API key to the .env file."""
    env_var = PROVIDER_KEY_NAMES.get(provider)
    if not env_var:
        raise ValueError(f"Unknown provider: {provider}")

    # Also set in current process
    os.environ[env_var] = key_value

    # Read existing .env content
    existing_lines: list[str] = []
    if ENV_FILE.exists():
        existing_lines = ENV_FILE.read_text().splitlines()

    # Update or append
    found = False
    new_lines: list[str] = []
    for line in existing_lines:
        if line.strip().startswith(f"{env_var}="):
            new_lines.append(f'{env_var}="{key_value}"')
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")  # blank line separator
        new_lines.append(f'# {provider.capitalize()} API key')
        new_lines.append(f'{env_var}="{key_value}"')

    ENV_FILE.write_text("\n".join(new_lines) + "\n")


def remove_key(provider: str) -> bool:
    """Remove an API key from the .env file and environment."""
    env_var = PROVIDER_KEY_NAMES.get(provider)
    if not env_var:
        return False

    os.environ.pop(env_var, None)

    if not ENV_FILE.exists():
        return False

    lines = ENV_FILE.read_text().splitlines()
    new_lines = [
        ln for ln in lines
        if not ln.strip().startswith(f"{env_var}=")
    ]
    ENV_FILE.write_text("\n".join(new_lines) + "\n")
    return True


def get_key(provider: str) -> str | None:
    """Get an API key — checks os.environ first, then .env file."""
    env_var = PROVIDER_KEY_NAMES.get(provider)
    if not env_var:
        return None
    # Check env first
    val = os.environ.get(env_var)
    if val:
        return val
    # Check .env file
    env_vars = load_env_file()
    val = env_vars.get(env_var)
    if val:
        os.environ[env_var] = val  # cache in env
    return val


def has_key(provider: str) -> bool:
    """Check if a provider has an API key configured."""
    return bool(get_key(provider))


def get_key_status() -> dict[str, dict]:
    """Get the status of all provider API keys."""
    result = {}
    for provider, env_var in PROVIDER_KEY_NAMES.items():
        key = get_key(provider)
        result[provider] = {
            "env_var": env_var,
            "set": bool(key),
            "masked": mask_key(key) if key else "(not set)",
            "url": PROVIDER_KEY_URLS.get(provider, ""),
        }
    return result
