"""Frontier LLM providers — all using OpenAI-compatible chat completions API.

Each provider is a thin subclass of OpenAICompatProvider with
provider-specific base URL, models, and key configuration.
"""
import json
import time
import httpx
from fastapi import WebSocket

from agent_team.llm.openai_compat import OpenAICompatProvider
from agent_team.llm.base import TokenStats, SessionTokenTracker
from agent_team.llm.keys import get_key, has_key, PROVIDER_KEY_URLS


# ── OpenAI ───────────────────────────────────────────────────────────────────

class OpenAIProvider(OpenAICompatProvider):
    name = "openai"
    api_base = "https://api.openai.com/v1"
    key_provider = "openai"
    default_model = "gpt-4o"
    max_tokens = 4096
    models = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "o1",
        "o1-mini",
        "o1-pro",
        "o3-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ]


# ── Anthropic ────────────────────────────────────────────────────────────────

class AnthropicProvider(OpenAICompatProvider):
    """Anthropic Claude — uses the Messages API (not OpenAI format).

    Anthropic uses a different header (x-api-key) and slightly
    different request/response format, so we override the key methods.
    """
    name = "anthropic"
    api_base = "https://api.anthropic.com/v1"
    key_provider = "anthropic"
    default_model = "claude-sonnet-4-20250514"
    max_tokens = 4096
    models = [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-haiku-4-20250514",
    ]

    def _get_headers(self) -> dict:
        key = get_key(self.key_provider)
        return {
            "Content-Type": "application/json",
            "x-api-key": key or "",
            "anthropic-version": "2023-06-01",
        }

    def _get_chat_url(self) -> str:
        return f"{self.api_base}/messages"

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict],
        ws: WebSocket,
        agent_name: str,
        agent_color: str = "#ffffff",
        temperature: float = 0.3,
        token_tracker: SessionTokenTracker | None = None,
    ) -> str:
        model = self._active_model
        full_response = ""

        # Anthropic format: system is a top-level field, not in messages
        chat_messages = []
        for msg in messages:
            chat_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        payload = {
            "model": model,
            "system": system_prompt,
            "messages": chat_messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        await ws.send_json({
            "type": "agent_start",
            "agent": agent_name,
            "color": agent_color,
            "model": model,
            "provider": self.name,
        })

        agent_stats = TokenStats()
        start_time = time.monotonic_ns()

        try:
            if not has_key(self.key_provider):
                await ws.send_json({
                    "type": "error",
                    "agent": agent_name,
                    "content": (
                        f"No API key for {self.name}. "
                        f"Run /key {self.key_provider} <your-key> to configure, "
                        f"or get one at: {PROVIDER_KEY_URLS.get(self.key_provider, '')}"
                    ),
                })
                await ws.send_json({"type": "agent_done", "agent": agent_name, "token_stats": {}})
                return ""

            from agent_team.llm.openai_compat import _get_client
            client = await _get_client(self.name)
            url = self._get_chat_url()
            headers = self._get_headers()

            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_msg = body.decode("utf-8", errors="replace")[:500]
                    await ws.send_json({
                        "type": "error",
                        "agent": agent_name,
                        "content": f"Anthropic API error ({response.status_code}): {error_msg}",
                    })
                    await ws.send_json({"type": "agent_done", "agent": agent_name, "token_stats": {}})
                    return ""

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        event_type = chunk.get("type", "")

                        if event_type == "content_block_delta":
                            delta = chunk.get("delta", {})
                            token = delta.get("text", "")
                            if token:
                                full_response += token
                                await ws.send_json({
                                    "type": "token",
                                    "agent": agent_name,
                                    "content": token,
                                })

                        elif event_type == "message_delta":
                            usage = chunk.get("usage", {})
                            if usage:
                                agent_stats.completion_tokens = usage.get("output_tokens", 0)

                        elif event_type == "message_start":
                            msg = chunk.get("message", {})
                            usage = msg.get("usage", {})
                            if usage:
                                agent_stats.prompt_tokens = usage.get("input_tokens", 0)

                    except json.JSONDecodeError:
                        continue

        except Exception as e:
            await ws.send_json({
                "type": "error",
                "agent": agent_name,
                "content": f"Anthropic error: {str(e)}",
            })

        elapsed_ns = time.monotonic_ns() - start_time
        agent_stats.total_tokens = agent_stats.prompt_tokens + agent_stats.completion_tokens
        agent_stats.eval_duration_ns = elapsed_ns

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

    async def call(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.3,
    ) -> str:
        chat_messages = []
        for msg in messages:
            chat_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        payload = {
            "model": self._active_model,
            "system": system_prompt,
            "messages": chat_messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        from agent_team.llm.openai_compat import _get_client
        client = await _get_client(self.name)
        r = await client.post(
            self._get_chat_url(),
            json=payload,
            headers=self._get_headers(),
        )
        r.raise_for_status()
        data = r.json()

        content_blocks = data.get("content", [])
        texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        return "".join(texts)


# ── Google Gemini ────────────────────────────────────────────────────────────

class GoogleProvider(OpenAICompatProvider):
    """Google Gemini — uses OpenAI-compatible endpoint."""
    name = "google"
    api_base = "https://generativelanguage.googleapis.com/v1beta/openai"
    key_provider = "google"
    default_model = "gemini-2.5-flash"
    max_tokens = 4096
    models = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ]


# ── Mistral ──────────────────────────────────────────────────────────────────

class MistralProvider(OpenAICompatProvider):
    name = "mistral"
    api_base = "https://api.mistral.ai/v1"
    key_provider = "mistral"
    default_model = "mistral-large-latest"
    max_tokens = 4096
    models = [
        "mistral-large-latest",
        "mistral-medium-latest",
        "mistral-small-latest",
        "codestral-latest",
        "open-mistral-nemo",
        "open-mixtral-8x22b",
    ]


# ── Groq ─────────────────────────────────────────────────────────────────────

class GroqProvider(OpenAICompatProvider):
    """Groq — blazing fast inference."""
    name = "groq"
    api_base = "https://api.groq.com/openai/v1"
    key_provider = "groq"
    default_model = "llama-3.3-70b-versatile"
    max_tokens = 4096
    models = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
        "qwen-qwq-32b",
    ]


# ── DeepSeek ─────────────────────────────────────────────────────────────────

class DeepSeekProvider(OpenAICompatProvider):
    name = "deepseek"
    api_base = "https://api.deepseek.com"
    key_provider = "deepseek"
    default_model = "deepseek-chat"
    max_tokens = 4096
    models = [
        "deepseek-chat",
        "deepseek-reasoner",
    ]


# ── Cohere ───────────────────────────────────────────────────────────────────

class CohereProvider(OpenAICompatProvider):
    """Cohere Command — uses OpenAI-compatible chat endpoint."""
    name = "cohere"
    api_base = "https://api.cohere.com/v2"
    key_provider = "cohere"
    default_model = "command-r-plus"
    max_tokens = 4096
    models = [
        "command-r-plus",
        "command-r",
        "command-a-03-2025",
    ]

    def _get_headers(self) -> dict:
        key = get_key(self.key_provider)
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key or ''}",
        }


# ── Together AI ──────────────────────────────────────────────────────────────

class TogetherProvider(OpenAICompatProvider):
    """Together AI — run open-source models via API."""
    name = "together"
    api_base = "https://api.together.xyz/v1"
    key_provider = "together"
    default_model = "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"
    max_tokens = 4096
    models = [
        "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "Qwen/Qwen2.5-Coder-32B-Instruct",
        "mistralai/Mixtral-8x22B-Instruct-v0.1",
        "deepseek-ai/DeepSeek-V3",
        "google/gemma-2-27b-it",
    ]
