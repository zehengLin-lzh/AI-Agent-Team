"""Mock implementations for testing the agent pipeline without real LLM/MCP calls."""
from __future__ import annotations

from agent_team.llm.base import LLMProvider, TokenStats, SessionTokenTracker


class MockLLMProvider(LLMProvider):
    """Returns canned responses. Tracks all calls for assertion."""

    name = "mock"

    def __init__(self, responses: dict[str, str] | None = None, default: str = "Mock response."):
        self._responses = responses or {}
        self._default = default
        self._active_model = "mock-model"
        self.calls: list[dict] = []

    def get_active_model(self) -> str:
        return self._active_model

    def set_active_model(self, model: str) -> None:
        self._active_model = model

    async def list_models(self) -> list[str]:
        return ["mock-model"]

    async def health_check(self) -> dict:
        return {"status": "ok", "provider": "mock"}

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict],
        emitter,
        agent_name: str,
        agent_color: str = "#ffffff",
        temperature: float = 0.3,
        token_tracker: SessionTokenTracker | None = None,
        display_name: str = "",
        model_override: str | None = None,
    ) -> str:
        self.calls.append({
            "method": "stream",
            "agent_name": agent_name,
            "system_prompt": system_prompt[:200],
            "messages_count": len(messages),
        })

        # Look up response by agent name or use default
        response = self._responses.get(agent_name, self._default)

        await emitter.emit("agent_start", {
            "agent": agent_name,
            "color": agent_color,
            "model": self._active_model,
            "provider": "mock",
        })

        # Emit tokens one word at a time
        for word in response.split():
            await emitter.emit("token", {
                "agent": agent_name,
                "content": word + " ",
            })

        stats = TokenStats(
            prompt_tokens=len(system_prompt) // 4,
            completion_tokens=len(response) // 4,
            total_tokens=(len(system_prompt) + len(response)) // 4,
        )
        if token_tracker:
            token_tracker.record(agent_name, stats)

        await emitter.emit("agent_done", {
            "agent": agent_name,
            "token_stats": {
                "prompt_tokens": stats.prompt_tokens,
                "completion_tokens": stats.completion_tokens,
                "total_tokens": stats.total_tokens,
                "tokens_per_second": 100.0,
            },
        })
        return response

    async def call(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.3,
        model_override: str | None = None,
    ) -> str:
        self.calls.append({
            "method": "call",
            "system_prompt": system_prompt[:200],
            "messages_count": len(messages),
        })
        return self._default
