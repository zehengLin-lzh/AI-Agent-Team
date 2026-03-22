"""Memory system types."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MemoryEntry:
    id: str
    content: str
    content_type: str = "text"  # text, code, plan, insight
    source: str = "session"  # session, learned, skill
    session_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    score: float = 0.0


@dataclass
class SearchResult:
    content: str
    score: float
    source: str
    content_type: str
    session_id: str | None = None


@dataclass
class LearnedPattern:
    id: str
    category: str  # error_fix, best_practice, architecture_pattern, preference
    description: str
    source_session_id: str | None = None
    confidence: float = 0.5
    times_applied: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
