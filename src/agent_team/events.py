"""Event emitter abstraction — decouples agent pipeline from transport layer.

Replaces the hard WebSocket dependency with a protocol-based approach:
- WebSocketEmitter: for real-time CLI/UI streaming (wraps WebSocket + lock)
- CallbackEmitter: for HTTP API, testing, and embedding
- NullEmitter: for batch/headless execution
"""
import asyncio
import json
from typing import Protocol, runtime_checkable


# ── Event type constants ────────────────────────────────────────────────────
# These match the 11 message types used in the current WebSocket protocol.

EVT_STATUS = "status"
EVT_AGENT_START = "agent_start"
EVT_TOKEN = "token"
EVT_AGENT_DONE = "agent_done"
EVT_AGENT_OUTPUT = "agent_output"
EVT_TOOL_RESULTS = "tool_results"
EVT_FILE_CHANGES = "file_changes"
EVT_WAITING_FOR_USER = "waiting_for_user"
EVT_MEMORY_CONTEXT = "memory_context"
EVT_COMPLETE = "complete"
EVT_ERROR = "error"
EVT_COMPLEXITY = "complexity"


# ── Protocol ────────────────────────────────────────────────────────────────

@runtime_checkable
class EventEmitter(Protocol):
    """Abstract event emitter — the only interface agents and providers need."""

    async def emit(self, event_type: str, data: dict) -> None:
        """Emit an event to the consumer (UI, test harness, etc.)."""
        ...

    async def receive(self) -> dict:
        """Receive a message from the consumer (e.g., user input).

        Blocks until a message is available. Returns a dict with at least
        a 'content' key.  Implementations that don't support receiving
        should raise NotImplementedError.
        """
        ...


# ── WebSocket implementation ────────────────────────────────────────────────

class WebSocketEmitter:
    """Wraps a FastAPI WebSocket with an asyncio.Lock for parallel-safe sends.

    Drop-in replacement for the old _LockedWebSocket wrapper in runner.py.
    """

    def __init__(self, ws) -> None:
        self._ws = ws
        self._lock = asyncio.Lock()

    async def emit(self, event_type: str, data: dict) -> None:
        payload = {"type": event_type, **data}
        async with self._lock:
            await self._ws.send_json(payload)

    async def receive(self) -> dict:
        raw = await self._ws.receive_text()
        return json.loads(raw)


# ── Callback implementation ─────────────────────────────────────────────────

class CallbackEmitter:
    """Collects events via a callback and supports injected user responses.

    Usage for HTTP/testing:
        events = []
        emitter = CallbackEmitter(on_event=lambda t, d: events.append((t, d)))
        # ... run pipeline ...
        # events now contains all emitted events in order

    For user-input scenarios, enqueue responses before running:
        emitter.enqueue_response({"content": "yes, proceed"})
    """

    def __init__(self, on_event=None) -> None:
        self._on_event = on_event
        self._events: list[tuple[str, dict]] = []
        self._responses: asyncio.Queue[dict] = asyncio.Queue()

    async def emit(self, event_type: str, data: dict) -> None:
        self._events.append((event_type, data))
        if self._on_event:
            result = self._on_event(event_type, data)
            if asyncio.iscoroutine(result):
                await result

    async def receive(self) -> dict:
        return await self._responses.get()

    def enqueue_response(self, data: dict) -> None:
        """Pre-load a response for the next receive() call."""
        self._responses.put_nowait(data)

    @property
    def events(self) -> list[tuple[str, dict]]:
        """All emitted events in order."""
        return self._events


# ── Null implementation ─────────────────────────────────────────────────────

class NullEmitter:
    """Discards all events. For batch/headless execution."""

    async def emit(self, event_type: str, data: dict) -> None:
        pass

    async def receive(self) -> dict:
        raise NotImplementedError(
            "NullEmitter does not support receiving messages. "
            "Use CallbackEmitter if user interaction is needed."
        )
