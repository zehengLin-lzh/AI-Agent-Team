"""Abstract LLM provider interface."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from fastapi import WebSocket


@dataclass
class TokenStats:
    """Token usage statistics from an LLM response."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    eval_duration_ns: int = 0  # nanoseconds

    @property
    def tokens_per_second(self) -> float:
        if self.eval_duration_ns > 0:
            return self.completion_tokens / (self.eval_duration_ns / 1e9)
        return 0.0


@dataclass
class SessionTokenTracker:
    """Tracks cumulative token usage across a session."""
    agents: dict[str, TokenStats] = field(default_factory=dict)

    @property
    def total_prompt(self) -> int:
        return sum(s.prompt_tokens for s in self.agents.values())

    @property
    def total_completion(self) -> int:
        return sum(s.completion_tokens for s in self.agents.values())

    @property
    def total(self) -> int:
        return self.total_prompt + self.total_completion

    def record(self, agent: str, stats: TokenStats) -> None:
        self.agents[agent] = stats

    def summary(self) -> dict:
        return {
            "total_prompt": self.total_prompt,
            "total_completion": self.total_completion,
            "total": self.total,
            "per_agent": {
                name: {
                    "prompt": s.prompt_tokens,
                    "completion": s.completion_tokens,
                    "total": s.prompt_tokens + s.completion_tokens,
                    "tokens_per_second": round(s.tokens_per_second, 1),
                }
                for name, s in self.agents.items()
            },
        }


class LLMProvider(ABC):
    """Abstract base for LLM providers (Ollama, HuggingFace, etc.)."""

    name: str = "base"

    @abstractmethod
    async def stream(
        self,
        system_prompt: str,
        messages: list[dict],
        ws: WebSocket,
        agent_name: str,
        agent_color: str = "#ffffff",
        temperature: float = 0.3,
        token_tracker: SessionTokenTracker | None = None,
        display_name: str = "",
    ) -> str:
        """Stream a response, sending tokens over WebSocket. Returns full response."""
        ...

    @abstractmethod
    async def call(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.3,
    ) -> str:
        """Non-streaming call. Returns full response text."""
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        """List available models for this provider."""
        ...

    @abstractmethod
    def get_active_model(self) -> str:
        """Get the currently active model name."""
        ...

    @abstractmethod
    def set_active_model(self, model: str) -> None:
        """Set the active model."""
        ...

    @abstractmethod
    async def health_check(self) -> dict:
        """Check provider health. Returns status dict."""
        ...
