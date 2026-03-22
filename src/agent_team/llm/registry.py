"""LLM provider registry — switch between Ollama, HuggingFace, OpenAI, Anthropic, etc."""
from fastapi import WebSocket

from agent_team.llm.base import LLMProvider, SessionTokenTracker

# Lazy-initialized providers
_providers: dict[str, LLMProvider] = {}
_active_provider: str = "ollama"


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
        CohereProvider, TogetherProvider,
    )
    _providers["openai"] = OpenAIProvider()
    _providers["anthropic"] = AnthropicProvider()
    _providers["google"] = GoogleProvider()
    _providers["mistral"] = MistralProvider()
    _providers["groq"] = GroqProvider()
    _providers["deepseek"] = DeepSeekProvider()
    _providers["cohere"] = CohereProvider()
    _providers["together"] = TogetherProvider()


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


async def stream_llm(
    system_prompt: str,
    messages: list[dict],
    ws: WebSocket,
    agent_name: str,
    agent_color: str = "#ffffff",
    temperature: float = 0.3,
    token_tracker: SessionTokenTracker | None = None,
) -> str:
    """Stream via the active provider."""
    return await get_provider().stream(
        system_prompt=system_prompt,
        messages=messages,
        ws=ws,
        agent_name=agent_name,
        agent_color=agent_color,
        temperature=temperature,
        token_tracker=token_tracker,
    )


async def call_llm(
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.3,
) -> str:
    """Non-streaming call via the active provider."""
    return await get_provider().call(
        system_prompt=system_prompt,
        messages=messages,
        temperature=temperature,
    )
