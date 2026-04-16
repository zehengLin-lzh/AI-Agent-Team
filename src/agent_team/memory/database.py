"""SQLite database for memory system with vector search support."""
import sqlite3
import struct
import uuid
from pathlib import Path
from datetime import datetime
from agent_team.config import MEMORY_DB_PATH, DATA_DIR
from agent_team.memory.types import MemoryEntry, LearnedPattern, UserFeedback


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize embedding to bytes for SQLite storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _deserialize_embedding(data: bytes) -> list[float]:
    """Deserialize embedding from bytes."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


class MemoryDB:
    def __init__(self, db_path: Path | None = None):
        _ensure_data_dir()
        self.db_path = db_path or MEMORY_DB_PATH
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self._try_load_vec_extension()

    def _try_load_vec_extension(self):
        """Try to load sqlite-vec extension for vector search."""
        self.has_vec = False
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self.has_vec = True
            # Create virtual table for vector search if not exists
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[768]
                )
            """)
            self.conn.commit()
        except Exception:
            # sqlite-vec not available, fall back to FTS-only search
            pass

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                mode TEXT NOT NULL,
                user_plan TEXT NOT NULL,
                summary TEXT,
                quality_score REAL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                source TEXT NOT NULL DEFAULT 'session',
                content TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'text',
                created_at TEXT NOT NULL,
                embedding BLOB,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS learned_patterns (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                source_session_id TEXT,
                confidence REAL DEFAULT 0.5,
                times_applied INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                embedding BLOB
            );

            CREATE TABLE IF NOT EXISTS user_feedback (
                id TEXT PRIMARY KEY,
                rule TEXT NOT NULL,
                rationale TEXT,
                category TEXT,
                source_session_id TEXT,
                source_message TEXT,
                trigger TEXT NOT NULL,
                confidence REAL DEFAULT 0.9,
                times_applied INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                embedding BLOB
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_active
                ON user_feedback(active, confidence DESC);
        """)
        # Create FTS5 tables if not exists
        try:
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    content, id UNINDEXED, source UNINDEXED, content_type UNINDEXED
                )
            """)
        except sqlite3.OperationalError:
            pass  # Already exists
        try:
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS user_feedback_fts
                    USING fts5(rule, rationale, id UNINDEXED)
            """)
        except sqlite3.OperationalError:
            pass  # Already exists
        self.conn.commit()

    def create_session(self, mode: str, user_plan: str) -> str:
        session_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, mode, user_plan) VALUES (?, ?, ?, ?)",
            (session_id, datetime.now().isoformat(), mode, user_plan),
        )
        self.conn.commit()
        return session_id

    def end_session(self, session_id: str, summary: str | None = None, quality_score: float | None = None):
        self.conn.execute(
            "UPDATE sessions SET ended_at = ?, summary = ?, quality_score = ? WHERE id = ?",
            (datetime.now().isoformat(), summary, quality_score, session_id),
        )
        self.conn.commit()

    def store_chunk(self, content: str, embedding: list[float] | None = None,
                    session_id: str | None = None, source: str = "session",
                    content_type: str = "text") -> str:
        chunk_id = str(uuid.uuid4())
        emb_bytes = _serialize_embedding(embedding) if embedding else None
        self.conn.execute(
            "INSERT INTO chunks (id, session_id, source, content, content_type, created_at, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chunk_id, session_id, source, content, content_type, datetime.now().isoformat(), emb_bytes),
        )
        # Insert into FTS index
        try:
            self.conn.execute(
                "INSERT INTO chunks_fts (content, id, source, content_type) VALUES (?, ?, ?, ?)",
                (content, chunk_id, source, content_type),
            )
        except Exception:
            pass
        # Insert into vector index
        if embedding and self.has_vec:
            try:
                self.conn.execute(
                    "INSERT INTO chunks_vec (id, embedding) VALUES (?, ?)",
                    (chunk_id, _serialize_embedding(embedding)),
                )
            except Exception:
                pass
        self.conn.commit()
        return chunk_id

    def store_pattern(self, pattern: LearnedPattern, embedding: list[float] | None = None):
        emb_bytes = _serialize_embedding(embedding) if embedding else None
        self.conn.execute(
            "INSERT OR REPLACE INTO learned_patterns "
            "(id, category, description, source_session_id, confidence, times_applied, created_at, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pattern.id, pattern.category, pattern.description,
             pattern.source_session_id, pattern.confidence,
             pattern.times_applied, pattern.created_at, emb_bytes),
        )
        self.conn.commit()

    def keyword_search(self, query: str, top_k: int = 10) -> list[tuple[str, str, float]]:
        """BM25 keyword search via FTS5. Returns (id, content, rank)."""
        try:
            rows = self.conn.execute(
                "SELECT id, content, rank FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, top_k),
            ).fetchall()
            return [(r[0], r[1], r[2]) for r in rows]
        except Exception:
            return []

    def vector_search(self, embedding: list[float], top_k: int = 10) -> list[tuple[str, float]]:
        """Vector similarity search. Returns (chunk_id, distance)."""
        if not self.has_vec:
            return []
        try:
            emb_bytes = _serialize_embedding(embedding)
            rows = self.conn.execute(
                "SELECT id, distance FROM chunks_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (emb_bytes, top_k),
            ).fetchall()
            return [(r[0], r[1]) for r in rows]
        except Exception:
            return []

    def get_chunk_by_id(self, chunk_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, session_id, source, content, content_type, created_at FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        if row:
            return dict(row)
        return None

    def get_session_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return row[0] if row else 0

    def get_pattern_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM learned_patterns").fetchone()
        return row[0] if row else 0

    def get_chunk_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return row[0] if row else 0

    def get_relevant_patterns(
        self,
        min_confidence: float = 0.4,
        limit: int = 10,
        category: str | None = None,
    ) -> list[dict]:
        """Get high-confidence learned patterns for injection into agent context."""
        if category:
            rows = self.conn.execute(
                "SELECT id, category, description, confidence, times_applied "
                "FROM learned_patterns WHERE category = ? AND confidence >= ? "
                "ORDER BY confidence DESC, times_applied DESC LIMIT ?",
                (category, min_confidence, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, category, description, confidence, times_applied "
                "FROM learned_patterns WHERE confidence >= ? "
                "ORDER BY confidence DESC, times_applied DESC LIMIT ?",
                (min_confidence, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def boost_pattern_confidence(self, pattern_id: str, delta: float = 0.1):
        self.conn.execute(
            "UPDATE learned_patterns SET confidence = MIN(1.0, confidence + ?), times_applied = times_applied + 1 WHERE id = ?",
            (delta, pattern_id),
        )
        self.conn.commit()

    # ── User Feedback ───────────────────────────────────────────────────────

    def create_feedback(
        self,
        rule: str,
        rationale: str | None,
        trigger: str,
        source_session_id: str | None = None,
        source_message: str | None = None,
        category: str | None = None,
        confidence: float = 0.9,
    ) -> str:
        """Store a user feedback entry. Deduplicates against existing rules."""
        existing_id = self.find_duplicate_feedback(rule)
        if existing_id:
            self.boost_feedback_confidence(existing_id)
            return existing_id

        feedback_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO user_feedback "
            "(id, rule, rationale, category, source_session_id, source_message, "
            "trigger, confidence, times_applied, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?)",
            (feedback_id, rule, rationale, category, source_session_id,
             source_message, trigger, confidence, now),
        )
        try:
            self.conn.execute(
                "INSERT INTO user_feedback_fts (rule, rationale, id) VALUES (?, ?, ?)",
                (rule, rationale or "", feedback_id),
            )
        except Exception:
            pass
        self.conn.commit()
        return feedback_id

    def get_relevant_feedback(
        self,
        min_confidence: float = 0.6,
        limit: int = 10,
        category: str | None = None,
    ) -> list[dict]:
        """Get high-confidence user feedback for injection into agent context."""
        if category:
            rows = self.conn.execute(
                "SELECT id, rule, rationale, category, confidence, times_applied, trigger "
                "FROM user_feedback WHERE active = 1 AND confidence >= ? AND category = ? "
                "ORDER BY confidence DESC, created_at DESC LIMIT ?",
                (min_confidence, category, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, rule, rationale, category, confidence, times_applied, trigger "
                "FROM user_feedback WHERE active = 1 AND confidence >= ? "
                "ORDER BY confidence DESC, created_at DESC LIMIT ?",
                (min_confidence, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_feedback(self, query: str, top_k: int = 5) -> list[dict]:
        """Full-text search over user feedback rules."""
        try:
            rows = self.conn.execute(
                "SELECT f.id, f.rule, f.rationale, f.category, f.confidence, f.times_applied "
                "FROM user_feedback_fts fts "
                "JOIN user_feedback f ON fts.id = f.id "
                "WHERE user_feedback_fts MATCH ? AND f.active = 1 "
                "ORDER BY fts.rank LIMIT ?",
                (query, top_k),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def deactivate_feedback(self, feedback_id: str) -> bool:
        """Deactivate a feedback entry (soft delete)."""
        cursor = self.conn.execute(
            "UPDATE user_feedback SET active = 0, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), feedback_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def boost_feedback_confidence(self, feedback_id: str, delta: float = 0.05) -> None:
        """Increase confidence and usage count for a feedback entry."""
        self.conn.execute(
            "UPDATE user_feedback SET confidence = MIN(1.0, confidence + ?), "
            "times_applied = times_applied + 1, updated_at = ? WHERE id = ?",
            (delta, datetime.now().isoformat(), feedback_id),
        )
        self.conn.commit()

    def find_duplicate_feedback(self, rule: str) -> str | None:
        """Check if a similar feedback rule already exists via FTS."""
        try:
            rows = self.conn.execute(
                "SELECT fts.id, fts.rank FROM user_feedback_fts fts "
                "JOIN user_feedback f ON fts.id = f.id "
                "WHERE user_feedback_fts MATCH ? AND f.active = 1 "
                "ORDER BY fts.rank LIMIT 1",
                (rule,),
            ).fetchall()
            if rows and rows[0][1] < -5.0:  # Strong BM25 match (more negative = better)
                return rows[0][0]
        except Exception:
            pass
        return None

    def list_active_feedback(self) -> list[dict]:
        """List all active feedback entries."""
        rows = self.conn.execute(
            "SELECT id, rule, rationale, category, confidence, times_applied, trigger, created_at "
            "FROM user_feedback WHERE active = 1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()
