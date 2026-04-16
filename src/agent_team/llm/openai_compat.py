"""OpenAI-compatible provider base.

Most frontier LLM APIs (OpenAI, Anthropic Messages, Google Gemini,
Mistral, Groq, DeepSeek, Together, Cohere) use the OpenAI chat
completions format. This base class handles the common streaming
and non-streaming logic.
"""
from __future__ import annotations

import json
import time
import httpx
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_team.events import EventEmitter

from agent_team.llm.base import LLMProvider, TokenStats, SessionTokenTracker
from agent_team.llm.keys import get_key, has_key, PROVIDER_KEY_URLS

_shared_clients: dict[str, httpx.AsyncClient] = {}


async def _get_client(provider_name: str) -> httpx.AsyncClient:
    if provider_name not in _shared_clients or _shared_clients[provider_name].is_closed:
        _shared_clients[provider_name] = httpx.AsyncClient(
            timeout=300.0,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _shared_clients[provider_name]


class OpenAICompatProvider(LLMProvider):
    """Base class for OpenAI-compatible API providers."""

    name: str = "openai_compat"
    api_base: str = ""
    default_model: str = ""
    models: list[str] = []
    key_provider: str = ""  # key name in PROVIDER_KEY_NAMES
    max_tokens: int = 4096

    def __init__(self):
        self._active_model: str = self.default_model

    def get_active_model(self) -> str:
        return self._active_model

    def set_active_model(self, model: str) -> None:
        self._active_model = model

    async def list_models(self) -> list[str]:
        return self.models

    def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        key = get_key(self.key_provider)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _get_chat_url(self) -> str:
        return f"{self.api_base}/chat/completions"

    async def health_check(self) -> dict:
        if not has_key(self.key_provider):
            return {
                "status": "no_key",
                "provider": self.name,
                "error": f"API key not set. Get one at: {PROVIDER_KEY_URLS.get(self.key_provider, '')}",
                "hint": f"Run /key {self.key_provider} <your-key> to configure",
            }
        try:
            client = await _get_client(self.name)
            # Quick test with a tiny request
            r = await client.post(
                self._get_chat_url(),
                headers=self._get_headers(),
                json={
                    "model": self._active_model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
                timeout=15.0,
            )
            if r.status_code in (200, 201):
                return {"status": "ok", "provider": self.name, "model": self._active_model}
            else:
                return {
                    "status": "error",
                    "provider": self.name,
                    "error": f"HTTP {r.status_code}: {r.text[:200]}",
                }
        except Exception as e:
            return {"status": "error", "provider": self.name, "error": str(e)}

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict],
        emitter: EventEmitter,
        agent_name: str,
        agent_color: str = "#ffffff",
        temperature: float = 0.3,
        token_tracker: SessionTokenTracker | None = None,
        display_name: str = "",
        model_override: str | None = None,
    ) -> str:
        model = model_override or self._active_model
        full_response = ""

        chat_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            chat_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        payload = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "top_p": 0.9,
            "stream": True,
        }

        start_msg: dict = {
            "agent": agent_name,
            "color": agent_color,
            "model": model,
            "provider": self.name,
        }
        if display_name:
            start_msg["display_name"] = display_name
        await emitter.emit("agent_start", start_msg)

        agent_stats = TokenStats()
        start_time = time.monotonic_ns()

        try:
            if not has_key(self.key_provider):
                await emitter.emit("error", {
                    "agent": agent_name,
                    "content": (
                        f"No API key for {self.name}. "
                        f"Run /key {self.key_provider} <your-key> to configure, "
                        f"or get one at: {PROVIDER_KEY_URLS.get(self.key_provider, '')}"
                    ),
                })
                await emitter.emit("agent_done", {"agent": agent_name, "token_stats": {}})
                return ""

            client = await _get_client(self.name)
            url = self._get_chat_url()
            headers = self._get_headers()

            # Retry with model rotation for rate limits (429) and transient errors (503).
            # Free-tier models have tight rate limits (~20 req/min). When hit,
            # rotate to a different free model rather than waiting on the same one.
            import asyncio as _asyncio
            _max_retries = 4
            _free_fallbacks = [m for m in getattr(self, 'models', [])
                               if m.endswith(":free") and m != payload["model"]]
            _fallback_idx = 0
            for _attempt in range(_max_retries + 1):
                _resp = client.stream("POST", url, json=payload, headers=headers)
                response = await _resp.__aenter__()
                if response.status_code in (429, 503) and _attempt < _max_retries:
                    await response.aclose()
                    # Try a different free model if available
                    if _free_fallbacks and _fallback_idx < len(_free_fallbacks):
                        alt_model = _free_fallbacks[_fallback_idx]
                        _fallback_idx += 1
                        payload["model"] = alt_model
                        await emitter.emit("status", {
                            "message": f"Rate limited → switching to {alt_model}",
                            "phase": "retry",
                        })
                        await _asyncio.sleep(1)
                    else:
                        wait = 5 * (_attempt + 1)  # 5s, 10s, 15s, 20s
                        await emitter.emit("status", {
                            "message": f"Rate limited, waiting {wait}s... ({_attempt+1}/{_max_retries})",
                            "phase": "retry",
                        })
                        await _asyncio.sleep(wait)
                    continue
                break

            try:
                if response.status_code != 200:
                    body = await response.aread()
                    error_msg = body.decode("utf-8", errors="replace")[:500]
                    await emitter.emit("error", {
                        "agent": agent_name,
                        "content": f"{self.name} API error ({response.status_code}): {error_msg}",
                    })
                    await emitter.emit("agent_done", {"agent": agent_name, "token_stats": {}})
                    return ""

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                full_response += token
                                await emitter.emit("token", {
                                    "agent": agent_name,
                                    "content": token,
                                })
                        usage = chunk.get("usage")
                        if usage:
                            agent_stats.prompt_tokens = usage.get("prompt_tokens", 0)
                            agent_stats.completion_tokens = usage.get("completion_tokens", 0)
                            agent_stats.total_tokens = usage.get("total_tokens", 0)
                    except json.JSONDecodeError:
                        continue
            finally:
                await response.aclose()

        except Exception as e:
            await emitter.emit("error", {
                "agent": agent_name,
                "content": f"{self.name} error: {str(e)}",
            })

        elapsed_ns = time.monotonic_ns() - start_time
        if agent_stats.completion_tokens == 0 and full_response:
            agent_stats.completion_tokens = max(1, len(full_response) // 4)
            agent_stats.total_tokens = agent_stats.prompt_tokens + agent_stats.completion_tokens
        agent_stats.eval_duration_ns = elapsed_ns

        if token_tracker:
            token_tracker.record(agent_name, agent_stats)

        await emitter.emit("agent_done", {
            "agent": agent_name,
            "token_stats": {
                "prompt_tokens": agent_stats.prompt_tokens,
                "completion_tokens": agent_stats.completion_tokens,
                "total_tokens": agent_stats.total_tokens,
                "tokens_per_second": round(agent_stats.tokens_per_second, 1),
            },
        })
        return full_response

    async def call(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.3,
        model_override: str | None = None,
    ) -> str:
        model = model_override or self._active_model
        chat_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            chat_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        payload = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "top_p": 0.9,
            "stream": False,
        }

        client = await _get_client(self.name)
        r = await client.post(
            self._get_chat_url(),
            json=payload,
            headers=self._get_headers(),
        )
        r.raise_for_status()
        data = r.json()

        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return ""
