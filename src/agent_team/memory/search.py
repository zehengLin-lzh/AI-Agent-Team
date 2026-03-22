"""Hybrid BM25 + vector search with temporal decay."""
import math
from datetime import datetime
from agent_team.memory.database import MemoryDB
from agent_team.memory.embeddings import get_embedding
from agent_team.memory.types import SearchResult


class HybridSearch:
    def __init__(self, db: MemoryDB | None = None):
        self.db = db or MemoryDB()
        self.vector_weight = 0.6
        self.keyword_weight = 0.4
        self.half_life_days = 30  # temporal decay half-life

    def _temporal_decay(self, created_at: str) -> float:
        """Apply temporal decay — newer memories score higher."""
        try:
            created = datetime.fromisoformat(created_at)
            age_days = (datetime.now() - created).total_seconds() / 86400
            decay_lambda = math.log(2) / self.half_life_days
            return math.exp(-decay_lambda * age_days)
        except Exception:
            return 0.5

    async def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Hybrid search combining vector similarity and BM25 keyword search."""
        scores: dict[str, float] = {}  # chunk_id → combined score
        chunk_data: dict[str, dict] = {}  # chunk_id → chunk info

        # 1. Keyword search (BM25)
        keyword_results = self.db.keyword_search(query, top_k=top_k * 2)
        if keyword_results:
            max_rank = max(abs(r[2]) for r in keyword_results) or 1.0
            for chunk_id, content, rank in keyword_results:
                normalized = 1.0 - (abs(rank) / max_rank)  # normalize to 0-1
                scores[chunk_id] = self.keyword_weight * normalized
                chunk = self.db.get_chunk_by_id(chunk_id)
                if chunk:
                    chunk_data[chunk_id] = chunk

        # 2. Vector search
        try:
            query_embedding = await get_embedding(query)
            if query_embedding:
                vec_results = self.db.vector_search(query_embedding, top_k=top_k * 2)
                if vec_results:
                    max_dist = max(r[1] for r in vec_results) or 1.0
                    for chunk_id, distance in vec_results:
                        similarity = 1.0 - (distance / max_dist)  # convert distance to similarity
                        scores[chunk_id] = scores.get(chunk_id, 0) + self.vector_weight * similarity
                        if chunk_id not in chunk_data:
                            chunk = self.db.get_chunk_by_id(chunk_id)
                            if chunk:
                                chunk_data[chunk_id] = chunk
        except Exception:
            pass  # Vector search not available, rely on keyword only

        # 3. Apply temporal decay
        for chunk_id in scores:
            if chunk_id in chunk_data:
                decay = self._temporal_decay(chunk_data[chunk_id].get("created_at", ""))
                scores[chunk_id] *= decay

        # 4. Sort and return top-K
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:top_k]
        results = []
        for chunk_id in sorted_ids:
            chunk = chunk_data.get(chunk_id, {})
            results.append(SearchResult(
                content=chunk.get("content", ""),
                score=scores[chunk_id],
                source=chunk.get("source", "unknown"),
                content_type=chunk.get("content_type", "text"),
                session_id=chunk.get("session_id"),
            ))
        return results
