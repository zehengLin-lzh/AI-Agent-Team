"""Ollama embedding client for local vector embeddings."""
import httpx
from agent_team.config import OLLAMA_BASE_URL, EMBEDDING_MODEL


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector for text using Ollama."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("embedding", [])


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embeddings for multiple texts."""
    results = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for text in texts:
            try:
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/embeddings",
                    json={"model": EMBEDDING_MODEL, "prompt": text},
                )
                response.raise_for_status()
                data = response.json()
                results.append(data.get("embedding", []))
            except Exception:
                results.append([])
    return results
