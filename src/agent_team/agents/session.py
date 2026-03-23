"""Session context — persists conversation history within a CLI session."""
import time
from dataclasses import dataclass, field


@dataclass
class SessionMessage:
    role: str  # "user", "agent", "system"
    content: str
    agent: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class SessionContext:
    """Tracks conversation history within a single CLI session."""

    def __init__(self, max_history: int = 20):
        self.messages: list[SessionMessage] = []
        self.scan_context: str = ""  # Repo scan results
        self.max_history = max_history
        self.session_id = f"session_{int(time.time())}"

    def add_user_message(self, content: str):
        self.messages.append(SessionMessage(role="user", content=content))
        self._trim()

    def add_agent_output(self, agent: str, content: str):
        self.messages.append(SessionMessage(role="agent", content=content, agent=agent))
        self._trim()

    def add_scan_result(self, scan_text: str):
        """Store repo scan results as persistent context."""
        self.scan_context = scan_text

    def get_context_summary(self, max_tokens: int = 6000) -> str:
        """Build a summary of session history for injection into agent context."""
        parts = []

        if self.scan_context:
            parts.append(f"## Repository Context\n{self.scan_context}")

        if self.messages:
            parts.append("## Session History")
            for msg in self.messages[-10:]:  # Last 10 messages
                if msg.role == "user":
                    parts.append(f"User: {msg.content[:500]}")
                elif msg.role == "agent":
                    # Summarize agent outputs to save tokens
                    summary = msg.content[:300]
                    if len(msg.content) > 300:
                        summary += "..."
                    parts.append(f"{msg.agent}: {summary}")

        text = "\n\n".join(parts)
        # Rough token limit
        max_chars = max_tokens * 4
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [session history truncated]"
        return text

    def _trim(self):
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history:]

    def clear(self):
        self.messages.clear()
        self.scan_context = ""
