"""Knowledge extraction from completed sessions."""
from agent_team.memory.database import MemoryDB
from agent_team.memory.embeddings import get_embedding
from agent_team.memory.indexer import index_session
from agent_team.memory.types import LearnedPattern
from agent_team.ollama.client import call_ollama
import uuid


SUMMARY_PROMPT = """You are a knowledge extraction agent. Analyze this session transcript and produce:

1. A brief summary (2-3 sentences) of what was accomplished
2. Key patterns or insights that would be useful for similar future tasks
3. A quality assessment (0.0 to 1.0) based on:
   - Did the output address the request fully?
   - Were there issues that needed fixing?
   - Was the approach efficient?

Output format (use exactly):
SUMMARY: <2-3 sentence summary>

PATTERNS:
- [category] <pattern description>

Categories: error_fix, best_practice, architecture_pattern, coding_pattern, preference

QUALITY: <0.0-1.0>
"""


def _parse_extraction(text: str) -> tuple[str, list[dict], float]:
    """Parse the extraction output into structured data."""
    summary = ""
    patterns = []
    quality = 0.5

    import re

    # Extract summary
    match = re.search(r'SUMMARY:\s*(.+?)(?=\nPATTERNS:|\Z)', text, re.DOTALL)
    if match:
        summary = match.group(1).strip()

    # Extract patterns
    pattern_section = re.search(r'PATTERNS:\s*\n(.*?)(?=\nQUALITY:|\Z)', text, re.DOTALL)
    if pattern_section:
        for line in pattern_section.group(1).strip().split('\n'):
            line = line.strip().lstrip('- ')
            cat_match = re.match(r'\[(\w+)\]\s*(.+)', line)
            if cat_match:
                patterns.append({
                    'category': cat_match.group(1),
                    'description': cat_match.group(2).strip(),
                })

    # Extract quality score
    quality_match = re.search(r'QUALITY:\s*([\d.]+)', text)
    if quality_match:
        try:
            quality = float(quality_match.group(1))
            quality = max(0.0, min(1.0, quality))
        except ValueError:
            quality = 0.5

    return summary, patterns, quality


async def extract_session_knowledge(
    user_plan: str,
    mode: str,
    phase_outputs: dict[str, str],
    db: MemoryDB | None = None,
) -> dict:
    """Extract and store knowledge from a completed session.
    Returns stats about what was extracted."""
    db = db or MemoryDB()

    # Create session record
    session_id = db.create_session(mode=mode, user_plan=user_plan)

    # Build transcript from all phase outputs
    transcript_parts = [f"User request: {user_plan}\n"]
    for agent, output in phase_outputs.items():
        if not agent.startswith("_"):  # Skip internal keys
            transcript_parts.append(f"[{agent}]\n{output}\n")
    transcript = "\n---\n".join(transcript_parts)

    # Index the transcript into memory chunks
    chunks_stored = await index_session(session_id, transcript, db=db)

    # Use Ollama to extract summary and patterns
    summary = ""
    patterns_stored = 0
    quality = 0.5

    try:
        extraction = await call_ollama(
            system_prompt=SUMMARY_PROMPT,
            messages=[{"role": "user", "content": transcript[:8000]}],  # Cap input
            temperature=0.2,
        )
        summary, patterns, quality = _parse_extraction(extraction)

        # Store learned patterns
        for p in patterns:
            pattern = LearnedPattern(
                id=str(uuid.uuid4()),
                category=p['category'],
                description=p['description'],
                source_session_id=session_id,
            )
            try:
                embedding = await get_embedding(p['description'])
                db.store_pattern(pattern, embedding=embedding if embedding else None)
            except Exception:
                db.store_pattern(pattern)
            patterns_stored += 1

    except Exception:
        pass  # Extraction is best-effort

    # End session with summary and quality
    db.end_session(session_id, summary=summary, quality_score=quality)

    return {
        "session_id": session_id,
        "chunks_stored": chunks_stored,
        "patterns_stored": patterns_stored,
        "quality_score": quality,
        "summary": summary,
    }
