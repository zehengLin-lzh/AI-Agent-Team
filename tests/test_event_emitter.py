"""Tests for the EventEmitter abstraction layer."""
import asyncio
import pytest

from agent_team.events import (
    EventEmitter,
    WebSocketEmitter,
    CallbackEmitter,
    NullEmitter,
    EVT_STATUS, EVT_TOKEN, EVT_AGENT_START, EVT_AGENT_DONE,
)


# ── CallbackEmitter tests ──────────────────────────────────────────────────

class TestCallbackEmitter:
    def test_protocol_compliance(self):
        """CallbackEmitter satisfies the EventEmitter protocol."""
        emitter = CallbackEmitter()
        assert isinstance(emitter, EventEmitter)

    @pytest.mark.asyncio
    async def test_collects_events_in_order(self):
        emitter = CallbackEmitter()
        await emitter.emit("status", {"message": "first"})
        await emitter.emit("token", {"content": "hello"})
        await emitter.emit("agent_done", {"agent": "test"})

        assert len(emitter.events) == 3
        assert emitter.events[0] == ("status", {"message": "first"})
        assert emitter.events[1] == ("token", {"content": "hello"})
        assert emitter.events[2] == ("agent_done", {"agent": "test"})

    @pytest.mark.asyncio
    async def test_callback_invoked(self):
        collected = []
        emitter = CallbackEmitter(on_event=lambda t, d: collected.append((t, d)))
        await emitter.emit("status", {"message": "test"})

        assert len(collected) == 1
        assert collected[0] == ("status", {"message": "test"})

    @pytest.mark.asyncio
    async def test_async_callback(self):
        collected = []

        async def on_event(t, d):
            collected.append((t, d))

        emitter = CallbackEmitter(on_event=on_event)
        await emitter.emit("token", {"content": "hi"})

        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_receive_with_enqueued_response(self):
        emitter = CallbackEmitter()
        emitter.enqueue_response({"content": "user says yes"})

        result = await emitter.receive()
        assert result == {"content": "user says yes"}

    @pytest.mark.asyncio
    async def test_receive_blocks_until_response(self):
        emitter = CallbackEmitter()
        received = []

        async def consumer():
            data = await emitter.receive()
            received.append(data)

        async def producer():
            await asyncio.sleep(0.05)
            emitter.enqueue_response({"content": "delayed"})

        await asyncio.gather(consumer(), producer())
        assert received == [{"content": "delayed"}]


# ── NullEmitter tests ──────────────────────────────────────────────────────

class TestNullEmitter:
    def test_protocol_compliance(self):
        emitter = NullEmitter()
        assert isinstance(emitter, EventEmitter)

    @pytest.mark.asyncio
    async def test_emit_does_nothing(self):
        emitter = NullEmitter()
        # Should not raise
        await emitter.emit("status", {"message": "discarded"})
        await emitter.emit("token", {"content": "gone"})

    @pytest.mark.asyncio
    async def test_receive_raises(self):
        emitter = NullEmitter()
        with pytest.raises(NotImplementedError):
            await emitter.receive()


# ── WebSocketEmitter tests ─────────────────────────────────────────────────

class _MockWebSocket:
    """Fake WebSocket for testing WebSocketEmitter."""

    def __init__(self):
        self.sent: list[dict] = []
        self._received: asyncio.Queue[str] = asyncio.Queue()

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        return await self._received.get()

    def enqueue_text(self, text: str) -> None:
        self._received.put_nowait(text)


class TestWebSocketEmitter:
    def test_protocol_compliance(self):
        ws = _MockWebSocket()
        emitter = WebSocketEmitter(ws)
        assert isinstance(emitter, EventEmitter)

    @pytest.mark.asyncio
    async def test_emit_sends_json_with_type(self):
        ws = _MockWebSocket()
        emitter = WebSocketEmitter(ws)

        await emitter.emit("status", {"message": "hello", "phase": "setup"})

        assert len(ws.sent) == 1
        assert ws.sent[0] == {
            "type": "status",
            "message": "hello",
            "phase": "setup",
        }

    @pytest.mark.asyncio
    async def test_receive_parses_json(self):
        ws = _MockWebSocket()
        emitter = WebSocketEmitter(ws)
        ws.enqueue_text('{"content": "user input"}')

        result = await emitter.receive()
        assert result == {"content": "user input"}

    @pytest.mark.asyncio
    async def test_concurrent_sends_are_serialized(self):
        """Multiple concurrent emit calls should not interleave."""
        ws = _MockWebSocket()
        emitter = WebSocketEmitter(ws)

        async def send_many(prefix: str, count: int):
            for i in range(count):
                await emitter.emit("token", {"content": f"{prefix}{i}"})

        await asyncio.gather(
            send_many("A", 10),
            send_many("B", 10),
        )

        assert len(ws.sent) == 20
        # Each message should be complete (no interleaving)
        for msg in ws.sent:
            assert "type" in msg
            assert msg["type"] == "token"
            assert "content" in msg
