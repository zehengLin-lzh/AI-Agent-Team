"""User feedback collection and quality adjustment."""
from agent_team.memory.database import MemoryDB


async def record_feedback(
    session_id: str,
    helpful: bool,
    db: MemoryDB | None = None,
):
    """Record user feedback for a session and adjust quality scores."""
    db = db or MemoryDB()

    # Adjust quality score based on feedback
    adjustment = 0.2 if helpful else -0.2
    row = db.conn.execute(
        "SELECT quality_score FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()

    if row:
        current = row[0] or 0.5
        new_score = max(0.0, min(1.0, current + adjustment))
        db.conn.execute(
            "UPDATE sessions SET quality_score = ? WHERE id = ?",
            (new_score, session_id),
        )
        db.conn.commit()
