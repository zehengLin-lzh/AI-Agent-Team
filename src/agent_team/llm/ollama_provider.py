"""Ollama LLM provider."""
import json
import httpx
from fastapi import WebSocket

from agent_team.config import OLLAMA_URL, OLLAMA_BASE_URL, MODEL
from agent_team.llm.base import LLMProvider, TokenStats, SessionTokenTracker

_shared_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=300.0,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _shared_client


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self):
        self._active_model: str = MODEL
        self._base_url: str = OLLAMA_BASE_URL
        self._chat_url: str = OLLAMA_URL

    def get_active_model(self) -> str:
        return self._active_model

    def set_active_model(self, model: str) -> None:
        self._active_model = model

    async def list_models(self) -> list[str]:
        try:
            client = await _get_client()
            r = await client.get(f"{self._base_url}/api/tags")
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    async def health_check(self) -> dict:
        try:
            client = await _get_client()
            r = await client.get(f"{self._base_url}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            has_model = any(self._active_model.split(":")[0] in m for m in models)
            return {
                "status": "ok",
                "provider": "ollama",
                "model": self._active_model,
                "model_available": has_model,
                "available_models": models,
            }
        except Exception as e:
            return {"status": "error", "provider": "ollama", "error": str(e)}

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
            "provider": "ollama",
        })
        agent_stats = TokenStats()
        try:
            client = await _get_client()
            async with client.stream("POST", self._chat_url, json=payload) as response:
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

    async def call(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.3,
    ) -> str:
        model = self._active_model
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
        client = await _get_client()
        r = await client.post(self._chat_url, json=payload)
        r.raise_for_status()
        data = r.json()
        return data.get("message", {}).get("content", "") or ""
