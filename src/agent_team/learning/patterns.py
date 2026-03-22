"""Pattern recognition and confidence tracking."""
from agent_team.memory.database import MemoryDB
from agent_team.memory.search import HybridSearch
from agent_team.memory.types import SearchResult


async def find_relevant_patterns(
    query: str,
    db: MemoryDB | None = None,
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[SearchResult]:
    """Find relevant past patterns for a new query."""
    searcher = HybridSearch(db=db)
    results = await searcher.search(query, top_k=top_k)
    return [r for r in results if r.score >= min_score]


def boost_pattern(db: MemoryDB, pattern_id: str, success: bool = True):
    """Update pattern confidence based on usage outcome."""
    delta = 0.1 if success else -0.15
    db.boost_pattern_confidence(pattern_id, delta)


def get_learning_stats(db: MemoryDB | None = None) -> dict:
    """Get statistics about the learning system."""
    db = db or MemoryDB()
    return {
        "total_sessions": db.get_session_count(),
        "total_chunks": db.get_chunk_count(),
        "total_patterns": db.get_pattern_count(),
    }
