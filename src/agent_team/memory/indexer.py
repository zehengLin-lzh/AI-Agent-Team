"""Session transcript indexer — chunks and embeds session data for memory."""
from agent_team.memory.database import MemoryDB
from agent_team.memory.embeddings import get_embedding


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into overlapping chunks of approximately chunk_size tokens.
    Uses ~4 chars per token approximation."""
    char_size = chunk_size * 4
    char_overlap = overlap * 4
    chunks = []
    start = 0
    while start < len(text):
        end = start + char_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - char_overlap
    return chunks


async def index_session(
    session_id: str,
    transcript: str,
    db: MemoryDB | None = None,
    content_type: str = "text",
) -> int:
    """Index a session transcript into memory. Returns number of chunks stored."""
    db = db or MemoryDB()
    chunks = chunk_text(transcript)
    stored = 0
    for chunk in chunks:
        try:
            embedding = await get_embedding(chunk)
            db.store_chunk(
                content=chunk,
                embedding=embedding if embedding else None,
                session_id=session_id,
                source="session",
                content_type=content_type,
            )
            stored += 1
        except Exception:
            # Store without embedding if embedding fails
            db.store_chunk(
                content=chunk,
                session_id=session_id,
                source="session",
                content_type=content_type,
            )
            stored += 1
    return stored
