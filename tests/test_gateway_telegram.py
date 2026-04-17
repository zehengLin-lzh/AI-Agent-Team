"""Tests for C3 Telegram Gateway (formatting + handler logic).

We do not instantiate a real aiogram bot — the handlers are factored so
SessionBuffer + pipeline dispatch are testable against a plain
CallbackEmitter.
"""
from __future__ import annotations

import pytest

from agent_team.gateway.formatting import (
    TELEGRAM_MAX_LEN,
    chunk_for_telegram,
    escape_markdown_v2,
    format_agent_event,
)
from agent_team.gateway.telegram import (
    SessionBuffer,
    TelegramGateway,
    _parse_allowlist,
)


# ── formatting ──────────────────────────────────────────────────────

class TestEscape:
    def test_plain_text_unchanged(self):
        assert escape_markdown_v2("hello world") == "hello world"

    def test_specials_escaped(self):
        escaped = escape_markdown_v2("a.b_c*d!")
        assert escaped == "a\\.b\\_c\\*d\\!"

    def test_empty_returns_empty(self):
        assert escape_markdown_v2("") == ""

    def test_backslash_escaped(self):
        assert escape_markdown_v2("a\\b") == "a\\\\b"


class TestChunking:
    def test_short_text_one_chunk(self):
        assert chunk_for_telegram("hello") == ["hello"]

    def test_empty_returns_single_empty(self):
        assert chunk_for_telegram("") == [""]

    def test_long_paragraph_splits(self):
        text = ("paragraph one.\n\n" + "x" * 3000 + "\n\n" + "tail")
        chunks = chunk_for_telegram(text, max_len=1500)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 1500 + 10  # small fence-wrapping tolerance

    def test_chunks_never_over_hard_limit(self):
        text = "x" * (TELEGRAM_MAX_LEN * 3)
        chunks = chunk_for_telegram(text)
        for chunk in chunks:
            assert len(chunk) <= TELEGRAM_MAX_LEN + 4

    def test_code_block_gets_closed_and_reopened(self):
        opening = "some prose\n\n```python\n" + "line\n" * 500
        tail = "\n```"
        text = opening + tail
        chunks = chunk_for_telegram(text, max_len=1000)
        # Every chunk's fence count must be even.
        for chunk in chunks:
            assert chunk.count("```") % 2 == 0


class TestEventFormatter:
    def test_status_with_phase(self):
        assert format_agent_event("status", {"phase": "THINK", "message": "planning"}) \
               == "[THINK] planning"

    def test_agent_start_with_model(self):
        line = format_agent_event("agent_start", {
            "display_name": "Soren", "model": "claude-sonnet-4",
        })
        assert "Soren" in line and "claude-sonnet-4" in line

    def test_agent_done_with_stats(self):
        line = format_agent_event("agent_done", {
            "agent": "Kai", "token_stats": {"total_tokens": 250},
        })
        assert "Kai" in line and "250" in line

    def test_token_event_not_surfaced(self):
        assert format_agent_event("token", {"content": "hi"}) is None

    def test_error_surfaced(self):
        line = format_agent_event("error", {"content": "rate limited"})
        assert line and "rate limited" in line

    def test_complete_surfaced(self):
        assert format_agent_event("complete", {}) == "✅ Done."


# ── SessionBuffer ────────────────────────────────────────────────────

class TestSessionBuffer:
    def test_tokens_aggregate_under_current_agent(self):
        buf = SessionBuffer()
        buf.on_event("agent_start", {"display_name": "Kai", "agent": "EXEC_KAI"})
        buf.on_event("token", {"content": "hello "})
        buf.on_event("token", {"content": "world"})
        buf.on_event("agent_done", {"agent": "EXEC_KAI", "token_stats": {"total_tokens": 10}})
        final = buf.final_text()
        assert "Kai" in final
        assert "hello world" in final

    def test_status_lines_capped_in_snapshot(self):
        buf = SessionBuffer()
        for i in range(50):
            buf.on_event("status", {"phase": "X", "message": f"m{i}"})
        snap = buf.status_snapshot()
        assert snap.count("\n") < 50

    def test_complete_event_marks_done(self):
        buf = SessionBuffer()
        buf.on_event("complete", {})
        assert buf.done is True

    def test_error_event_marks_done(self):
        buf = SessionBuffer()
        buf.on_event("error", {"content": "oops"})
        assert buf.done is True

    def test_empty_buffer_has_placeholder_snapshot(self):
        assert SessionBuffer().status_snapshot() == "Thinking..."

    def test_final_text_placeholder_when_no_output(self):
        assert SessionBuffer().final_text() == "(no output)"


# ── gateway config ───────────────────────────────────────────────────

class TestAllowlist:
    def test_parse_csv(self):
        assert _parse_allowlist("1,2,3") == {1, 2, 3}

    def test_parse_empty(self):
        assert _parse_allowlist(None) == set()
        assert _parse_allowlist("") == set()

    def test_parse_with_whitespace(self):
        assert _parse_allowlist(" 11 , 22 ") == {11, 22}

    def test_ignores_non_numeric(self):
        assert _parse_allowlist("1,abc,2") == {1, 2}


class TestGatewayAuth:
    def test_requires_token(self):
        with pytest.raises(ValueError):
            TelegramGateway(token="", allowed_user_ids={1})

    def test_is_authorized_checks_allowlist(self):
        gw = TelegramGateway("t", {42})
        assert gw.is_authorized(42) is True
        assert gw.is_authorized(99) is False

    def test_empty_allowlist_rejects_everyone(self):
        gw = TelegramGateway("t")
        assert gw.is_authorized(42) is False


# ── gateway_from_env ────────────────────────────────────────────────

class TestGatewayFromEnv:
    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        from agent_team.gateway.telegram import gateway_from_env
        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            gateway_from_env()

    def test_parses_allowlist_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111,222")
        from agent_team.gateway.telegram import gateway_from_env
        gw = gateway_from_env()
        assert gw.allowed_user_ids == {111, 222}
        assert gw.token == "fake-token"


# ── entry CLI importability (doesn't actually start the bot) ────────

class TestEntryImport:
    def test_entry_module_imports(self):
        import agent_team.gateway.entry as entry  # noqa: F401
        assert hasattr(entry, "app")
