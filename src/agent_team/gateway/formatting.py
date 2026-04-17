"""Telegram-specific formatting helpers.

Two concerns:

1. **Length**: Telegram caps a single ``sendMessage`` at 4096 chars. Long
   agent output must be split at safe boundaries (end-of-paragraph, and
   never inside a code block).

2. **Escaping**: Telegram's MarkdownV2 treats many characters as special.
   Unescaped underscores, brackets, etc. cause 400 Bad Request responses.
   We escape them defensively — the caller can pass plain text and get a
   MarkdownV2-safe string back.
"""
from __future__ import annotations

TELEGRAM_MAX_LEN = 4096

# Characters that must be escaped in MarkdownV2 (per Telegram Bot API docs).
# Backslashes have to be handled first so the escape itself doesn't get double-escaped.
_MARKDOWN_V2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def escape_markdown_v2(text: str) -> str:
    """Escape characters that have special meaning in MarkdownV2."""
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        if ch in _MARKDOWN_V2_SPECIAL:
            out.append("\\")
        out.append(ch)
    return "".join(out)


def chunk_for_telegram(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    r"""Split a long message into chunks that each fit Telegram's limit.

    Each returned chunk has balanced triple-backtick fences. If the source
    crosses a fence boundary we close the block with ```, carry the opener
    forward, and prepend ``` + newline to the next chunk so the code block
    renders uninterrupted.
    """
    if not text:
        return [""]
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    carryover_open = False  # did the previous chunk end mid-code-block?

    while len(remaining) > max_len:
        window = remaining[:max_len]
        split = window.rfind("\n\n")
        if split < max_len // 2:
            nl = window.rfind("\n")
            split = nl if nl >= max_len // 2 else max_len

        body = remaining[:split]
        chunk = ("```\n" + body) if carryover_open else body

        # If this chunk still ends inside a fence, close it and mark carryover.
        if chunk.count("```") % 2 == 1:
            chunk = chunk + "\n```"
            carryover_open = True
        else:
            carryover_open = False

        chunks.append(chunk.rstrip())
        remaining = remaining[split:].lstrip("\n")

    if remaining:
        tail = ("```\n" + remaining) if carryover_open else remaining
        chunks.append(tail.rstrip())
    return chunks


def format_agent_event(event_type: str, data: dict) -> str | None:
    """Render a subset of pipeline events as a short human-readable line.

    Returns ``None`` for events that aren't worth surfacing in Telegram
    (e.g. raw per-token streams, which would spam the chat).
    """
    if event_type == "status":
        msg = data.get("message", "")
        phase = data.get("phase", "")
        if not msg:
            return None
        return f"[{phase}] {msg}" if phase else msg
    if event_type == "agent_start":
        agent = data.get("display_name") or data.get("agent", "")
        model = data.get("model", "")
        return f"▶ {agent} ({model})" if model else f"▶ {agent}"
    if event_type == "agent_done":
        agent = data.get("agent", "")
        stats = data.get("token_stats", {})
        total = stats.get("total_tokens")
        if total is not None:
            return f"✔ {agent} ({total} tokens)"
        return f"✔ {agent}"
    if event_type == "error":
        return f"⚠ {data.get('content', 'error')[:500]}"
    if event_type == "complete":
        return "✅ Done."
    if event_type == "complexity":
        return f"Task complexity: {data.get('complexity', '?')}"
    return None
