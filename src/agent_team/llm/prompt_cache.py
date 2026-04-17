"""Prompt caching strategy per provider.

Anthropic exposes native ephemeral prompt caching via a ``cache_control``
block on its messages API — marking a system-prompt chunk with
``{"type": "ephemeral"}`` instructs the server to reuse the prefix across
requests for ~5 minutes (huge savings on long agent prompts).

OpenAI and OpenRouter do not expose controllable prompt caching from the
client side (OpenAI has an implicit cache but no flag to toggle). For those
providers this module is a no-op helper; we still expose ``get_cache_strategy``
so callers can introspect.
"""
from __future__ import annotations

from typing import Any

CACHE_STRATEGY_ANTHROPIC = "anthropic_native"
CACHE_STRATEGY_NONE = "none"

# Only cache system prompts larger than this many characters — very short
# prompts aren't worth the cache_write surcharge.
MIN_CACHEABLE_CHARS = 400


def get_cache_strategy(provider: str) -> str:
    if provider == "anthropic":
        return CACHE_STRATEGY_ANTHROPIC
    return CACHE_STRATEGY_NONE


def build_anthropic_system(system_prompt: str) -> str | list[dict[str, Any]]:
    """Return the value for the Anthropic ``system`` field.

    If the prompt is long enough to be worth caching, returns a list of
    content blocks with ``cache_control`` set on the final block. Otherwise
    returns the plain string so the request payload stays simple.
    """
    if not system_prompt:
        return system_prompt
    if len(system_prompt) < MIN_CACHEABLE_CHARS:
        return system_prompt
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
