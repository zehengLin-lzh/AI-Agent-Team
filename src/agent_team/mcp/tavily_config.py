"""Tavily web-search availability and configuration helpers."""
from __future__ import annotations

import os


def has_web_search() -> bool:
    """Return True if a Tavily API key is available in the environment.

    Loads keys from the project ``.env`` file first (via
    ``agent_team.llm.keys``) so the check works even before any LLM
    provider has been initialised.
    """
    from agent_team.llm.keys import load_keys_into_env

    load_keys_into_env()
    return bool(os.environ.get("TAVILY_API_KEY"))


def get_tavily_key_status() -> dict[str, str | bool]:
    """Return a status dict describing the Tavily key (safe for display).

    Keys in the returned dict:
        env_var  – the environment variable name
        set     – whether a key is configured
        masked  – the key value with middle chars replaced by ``*``
        url     – where to obtain a key
    """
    from agent_team.llm.keys import load_keys_into_env, mask_key

    load_keys_into_env()
    key = os.environ.get("TAVILY_API_KEY", "")
    return {
        "env_var": "TAVILY_API_KEY",
        "set": bool(key),
        "masked": mask_key(key) if key else "(not set)",
        "url": "https://tavily.com",
    }
