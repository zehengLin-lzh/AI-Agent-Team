"""HuggingFace LLM provider — supports HF Inference API and local TGI servers.

Supports two modes:
  1. HF Inference API (cloud) — needs HF_TOKEN env var
  2. Local TGI server — set HF_API_URL to your local endpoint

Popular open-source models:
  - mistralai/Mistral-7B-Instruct-v0.3
  - meta-llama/Meta-Llama-3.1-8B-Instruct
  - Qwen/Qwen2.5-Coder-7B-Instruct
  - microsoft/Phi-3.5-mini-instruct
  - google/gemma-2-9b-it
  - deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct
  - bigcode/starcoder2-15b
  - codellama/CodeLlama-13b-Instruct-hf
"""
from __future__ import annotations

import json
import os
import time
import httpx
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_team.events import EventEmitter

from agent_team.llm.base import LLMProvider, TokenStats, SessionTokenTracker

# Default HF models by use case
HF_DEFAULT_MODEL = "mistralai/Mistral-7B-Instruct-v0.3"

# Well-known coding models on HuggingFace
HF_RECOMMENDED_MODELS = [
    "mistralai/Mistral-7B-Instruct-v0.3",
    "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "Qwen/Qwen2.5-Coder-7B-Instruct",
    "microsoft/Phi-3.5-mini-instruct",
    "google/gemma-2-9b-it",
    "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct",
    "bigcode/starcoder2-15b",
    "codellama/CodeLlama-13b-Instruct-hf",
]

_shared_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=300.0,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _shared_client


def _get_hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


def _format_chat_messages(system_prompt: str, messages: list[dict]) -> list[dict]:
    """Format messages for HF chat completion API."""
    formatted = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        formatted.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })
    return formatted


class HuggingFaceProvider(LLMProvider):
    """HuggingFace Inference API / TGI provider."""

    name = "huggingface"

    def __init__(self):
        self._active_model: str = os.environ.get("HF_MODEL", HF_DEFAULT_MODEL)
        # Support custom TGI endpoint or default HF API
        self._api_url: str = os.environ.get(
            "HF_API_URL",
            "https://api-inference.huggingface.co",
        )
        self._is_local: bool = "localhost" in self._api_url or "127.0.0.1" in self._api_url

    def get_active_model(self) -> str:
        return self._active_model

    def set_active_model(self, model: str) -> None:
        self._active_model = model

    async def list_models(self) -> list[str]:
        """Return recommended models (HF has millions, so we curate)."""
        if self._is_local:
            # For local TGI, query the model info endpoint
            try:
                client = await _get_client()
                r = await client.get(f"{self._api_url}/info")
                data = r.json()
                return [data.get("model_id", self._active_model)]
            except Exception:
                return [self._active_model]
        return HF_RECOMMENDED_MODELS

    async def health_check(self) -> dict:
        token = _get_hf_token()
        if not token and not self._is_local:
            return {
                "status": "error",
                "provider": "huggingface",
                "error": "HF_TOKEN not set. Run: export HF_TOKEN='hf_...'",
            }
        try:
            client = await _get_client()
            if self._is_local:
                r = await client.get(f"{self._api_url}/health")
                return {
                    "status": "ok" if r.status_code == 200 else "error",
                    "provider": "huggingface (local TGI)",
                    "model": self._active_model,
                    "endpoint": self._api_url,
                }
            else:
                # Test with a minimal request
                headers = {"Authorization": f"Bearer {token}"}
                r = await client.get(
                    f"https://huggingface.co/api/models/{self._active_model}",
                    headers=headers,
                )
                if r.status_code == 200:
                    return {
                        "status": "ok",
                        "provider": "huggingface",
                        "model": self._active_model,
                        "endpoint": self._api_url,
                    }
                else:
                    return {
                        "status": "error",
                        "provider": "huggingface",
                        "error": f"Model check failed: {r.status_code}",
                    }
        except Exception as e:
            return {"status": "error", "provider": "huggingface", "error": str(e)}

    def _build_url(self) -> str:
        """Build the chat completions URL."""
        if self._is_local:
            return f"{self._api_url}/v1/chat/completions"
        return f"{self._api_url}/models/{self._active_model}/v1/chat/completions"

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        token = _get_hf_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

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
        formatted_messages = _format_chat_messages(system_prompt, messages)

        payload = {
            "model": model,
            "messages": formatted_messages,
            "max_tokens": 4096,
            "temperature": temperature,
            "top_p": 0.9,
            "stream": True,
        }

        start_msg: dict = {
            "agent": agent_name,
            "color": agent_color,
            "model": model,
            "provider": "huggingface",
        }
        if display_name:
            start_msg["display_name"] = display_name
        await emitter.emit("agent_start", start_msg)

        agent_stats = TokenStats()
        start_time = time.monotonic_ns()

        try:
            client = await _get_client()
            url = self._build_url()
            headers = self._build_headers()

            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_msg = body.decode("utf-8", errors="replace")[:500]
                    await emitter.emit("error", {
                        "agent": agent_name,
                        "content": f"HuggingFace API error ({response.status_code}): {error_msg}",
                    })
                    await emitter.emit("agent_done", {"agent": agent_name, "token_stats": {}})
                    return ""

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]  # strip "data: "
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

                        # Capture usage stats if present
                        usage = chunk.get("usage")
                        if usage:
                            agent_stats.prompt_tokens = usage.get("prompt_tokens", 0)
                            agent_stats.completion_tokens = usage.get("completion_tokens", 0)
                            agent_stats.total_tokens = usage.get("total_tokens", 0)
                    except json.JSONDecodeError:
                        continue

        except Exception as e:
            await emitter.emit("error", {
                "agent": agent_name,
                "content": f"HuggingFace error: {str(e)}",
            })

        # Estimate token stats if API didn't provide them
        elapsed_ns = time.monotonic_ns() - start_time
        if agent_stats.completion_tokens == 0 and full_response:
            # Rough estimate: ~4 chars per token
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
        formatted_messages = _format_chat_messages(system_prompt, messages)

        payload = {
            "model": model,
            "messages": formatted_messages,
            "max_tokens": 4096,
            "temperature": temperature,
            "top_p": 0.9,
            "stream": False,
        }

        client = await _get_client()
        url = self._build_url()
        headers = self._build_headers()

        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return ""
