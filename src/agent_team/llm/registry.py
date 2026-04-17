"""LLM provider registry — switch between Ollama, HuggingFace, OpenAI, Anthropic, etc."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agent_team.llm.base import LLMProvider, SessionTokenTracker
from agent_team.llm.rate_tracker import get_tracker

if TYPE_CHECKING:
    from agent_team.events import EventEmitter

logger = logging.getLogger(__name__)

# Providers covered by C2 Provider Industrialization (pre-call throttling,
# credential rotation, prompt caching, pricing). Other providers continue to
# use the unwrapped path.
INDUSTRIALIZED_PROVIDERS = frozenset({"anthropic", "openai", "openrouter"})

# Lazy-initialized providers
_providers: dict[str, LLMProvider] = {}
def _detect_default_provider() -> str:
    """Auto-detect best provider: prefer OpenRouter if key is set, else Ollama.

    Checks env vars and .env file. Does NOT make network calls at import time.
    """
    import os
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    try:
        from agent_team.llm.keys import has_key
        if has_key("openrouter"):
            return "openrouter"
    except Exception:
        pass
    return "ollama"

_active_provider: str = _detect_default_provider()


async def auto_fallback_provider() -> None:
    """Check if the active provider works; fall back to Ollama if it's rate-limited.

    Call this once at session start (not at import time) to avoid slow imports.
    """
    global _active_provider
    if _active_provider == "ollama":
        return  # Already local, nothing to fall back to
    try:
        provider = get_provider()
        health = await provider.health_check()
        status = health.get("status", "")
        error = health.get("error", "")
        if status != "ok" and ("429" in str(error) or "rate limit" in str(error).lower()):
            _active_provider = "ollama"
    except Exception:
        _active_provider = "ollama"


def _ensure_providers():
    """Initialize all providers on first access."""
    if _providers:
        return

    # Load API keys from .env
    from agent_team.llm.keys import load_keys_into_env
    load_keys_into_env()

    # Local providers
    from agent_team.llm.ollama_provider import OllamaProvider
    from agent_team.llm.huggingface_provider import HuggingFaceProvider
    _providers["ollama"] = OllamaProvider()
    _providers["huggingface"] = HuggingFaceProvider()

    # Frontier providers
    from agent_team.llm.providers import (
        OpenAIProvider, AnthropicProvider, GoogleProvider,
        MistralProvider, GroqProvider, DeepSeekProvider,
        CohereProvider, TogetherProvider, OpenRouterProvider,
    )
    _providers["openai"] = OpenAIProvider()
    _providers["anthropic"] = AnthropicProvider()
    _providers["google"] = GoogleProvider()
    _providers["mistral"] = MistralProvider()
    _providers["groq"] = GroqProvider()
    _providers["deepseek"] = DeepSeekProvider()
    _providers["cohere"] = CohereProvider()
    _providers["together"] = TogetherProvider()
    _providers["openrouter"] = OpenRouterProvider()


def get_provider(name: str | None = None) -> LLMProvider:
    """Get a provider by name, or the active provider."""
    _ensure_providers()
    if (name or _active_provider) not in _providers:
        raise ValueError(f"Unknown provider '{name or _active_provider}'. Available: {list(_providers.keys())}")
    return _providers[name or _active_provider]


def set_provider(name: str) -> None:
    """Switch the active provider."""
    global _active_provider
    _ensure_providers()
    if name not in _providers:
        raise ValueError(f"Unknown provider '{name}'. Available: {list(_providers.keys())}")
    _active_provider = name


def get_active_provider_name() -> str:
    return _active_provider


def list_providers() -> list[str]:
    _ensure_providers()
    return list(_providers.keys())


# ── Convenience functions (match the old API) ────────────────────────────────

def get_active_model() -> str:
    return get_provider().get_active_model()


def set_active_model(model: str) -> None:
    get_provider().set_active_model(model)


async def _wait_if_throttled(provider_name: str) -> None:
    """Pre-call throttle check; sleeps if provider is near its rate limit."""
    if provider_name not in INDUSTRIALIZED_PROVIDERS:
        return
    tracker = get_tracker(provider_name)
    need_wait, wait_s = tracker.should_throttle()
    if need_wait and wait_s > 0:
        logger.info(
            "rate_tracker: %s near limit, sleeping %.1fs", provider_name, wait_s,
        )
        await asyncio.sleep(min(wait_s, 30.0))  # cap sleep to keep UX sane


async def stream_llm(
    system_prompt: str,
    messages: list[dict],
    emitter: EventEmitter,
    agent_name: str,
    agent_color: str = "#ffffff",
    temperature: float = 0.3,
    token_tracker: SessionTokenTracker | None = None,
    display_name: str = "",
    model_override: str | None = None,
    # Legacy alias — callers using ws= keyword will still work
    ws: object | None = None,
) -> str:
    """Stream via the active provider."""
    # Support legacy ws= keyword for backward compat during migration
    target = emitter if emitter is not None else ws
    provider = get_provider()
    await _wait_if_throttled(provider.name)
    result = await provider.stream(
        system_prompt=system_prompt,
        messages=messages,
        emitter=target,
        agent_name=agent_name,
        agent_color=agent_color,
        temperature=temperature,
        token_tracker=token_tracker,
        display_name=display_name,
        model_override=model_override,
    )
    _record_usage(provider.name, agent_name, token_tracker, model_override)
    return result


async def call_llm(
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.3,
    model_override: str | None = None,
) -> str:
    """Non-streaming call via the active provider."""
    provider = get_provider()
    await _wait_if_throttled(provider.name)
    return await provider.call(
        system_prompt=system_prompt,
        messages=messages,
        temperature=temperature,
        model_override=model_override,
    )


def _record_usage(
    provider_name: str,
    agent_name: str,
    token_tracker: SessionTokenTracker | None,
    model_override: str | None,
) -> None:
    if provider_name not in INDUSTRIALIZED_PROVIDERS or token_tracker is None:
        return
    stats = token_tracker.agents.get(agent_name)
    if stats is None:
        return
    total = stats.prompt_tokens + stats.completion_tokens
    if total > 0:
        get_tracker(provider_name).record(total)
    try:
        from agent_team.llm.pricing import current_session_usage
        provider = _providers.get(provider_name)
        model = model_override or (provider.get_active_model() if provider else "")
        current_session_usage().record(
            provider_name,
            model,
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=stats.completion_tokens,
            cache_read_tokens=getattr(stats, "cache_read_tokens", 0),
            cache_write_tokens=getattr(stats, "cache_write_tokens", 0),
        )
    except Exception as e:  # pragma: no cover — never break the request path
        logger.debug("pricing.record failed: %s", e)
