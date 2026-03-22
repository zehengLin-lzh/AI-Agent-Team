"""Ollama LLM client with streaming and non-streaming support."""
import json
from dataclasses import dataclass, field
from typing import AsyncGenerator
import httpx
from fastapi import WebSocket
from agent_team.config import OLLAMA_URL, MODEL

_shared_client: httpx.AsyncClient | None = None

# Runtime-mutable model override (set via /model command)
_active_model: str | None = None


def get_active_model() -> str:
    return _active_model or MODEL


def set_active_model(model: str) -> None:
    global _active_model
    _active_model = model


@dataclass
class TokenStats:
    """Token usage statistics from an Ollama response."""
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


async def get_ollama_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=300.0,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _shared_client


async def stream_ollama(
    system_prompt: str,
    messages: list[dict],
    ws: WebSocket,
    agent_name: str,
    agent_color: str = "#ffffff",
    temperature: float = 0.3,
    token_tracker: SessionTokenTracker | None = None,
) -> str:
    """Stream a response from Ollama, sending tokens over WebSocket."""
    model = get_active_model()
    full_response = ""
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
        "options": {
            "temperature": temperature,
            "num_predict": 4096,
            "top_p": 0.9,
        },
    }
    await ws.send_json({
        "type": "agent_start",
        "agent": agent_name,
        "color": agent_color,
        "model": model,
    })
    agent_stats = TokenStats()
    try:
        client = await get_ollama_client()
        async with client.stream("POST", OLLAMA_URL, json=payload) as response:
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_response += token
                        await ws.send_json({
                            "type": "token",
                            "agent": agent_name,
                            "content": token,
                        })
                    # Capture token stats from the final chunk
                    if chunk.get("done"):
                        agent_stats.prompt_tokens = chunk.get("prompt_eval_count", 0)
                        agent_stats.completion_tokens = chunk.get("eval_count", 0)
                        agent_stats.total_tokens = agent_stats.prompt_tokens + agent_stats.completion_tokens
                        agent_stats.eval_duration_ns = chunk.get("eval_duration", 0)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        await ws.send_json({
            "type": "error",
            "agent": agent_name,
            "content": f"Ollama error: {str(e)}. Is Ollama running? Try: ollama serve",
        })

    if token_tracker:
        token_tracker.record(agent_name, agent_stats)

    await ws.send_json({
        "type": "agent_done",
        "agent": agent_name,
        "token_stats": {
            "prompt_tokens": agent_stats.prompt_tokens,
            "completion_tokens": agent_stats.completion_tokens,
            "total_tokens": agent_stats.total_tokens,
            "tokens_per_second": round(agent_stats.tokens_per_second, 1),
        },
    })
    return full_response


async def call_ollama(
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.3,
) -> str:
    """Non-streaming Ollama call. Returns full response text."""
    model = get_active_model()
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
        "options": {
            "temperature": temperature,
            "num_predict": 4096,
            "top_p": 0.9,
        },
    }
    client = await get_ollama_client()
    r = await client.post(OLLAMA_URL, json=payload)
    r.raise_for_status()
    data = r.json()
    return data.get("message", {}).get("content", "") or ""
