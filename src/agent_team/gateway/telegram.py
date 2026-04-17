"""Telegram bot gateway.

Wraps the existing ``AgentTeam`` pipeline with a Telegram chat surface. We
reuse ``CallbackEmitter`` so the agent code path is identical to the CLI /
web cases — only the transport changes.

aiogram is imported lazily so the module stays importable (and testable)
even when the optional dependency isn't installed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from agent_team.events import CallbackEmitter
from agent_team.gateway.formatting import (
    TELEGRAM_MAX_LEN,
    chunk_for_telegram,
    escape_markdown_v2,
    format_agent_event,
)

logger = logging.getLogger(__name__)


@dataclass
class SessionBuffer:
    """Per-chat accumulator for pipeline events and streamed tokens."""
    status_lines: list[str] = field(default_factory=list)
    final_output: list[str] = field(default_factory=list)
    current_agent: str = ""
    current_agent_tokens: list[str] = field(default_factory=list)
    done: bool = False

    def on_event(self, event_type: str, data: dict) -> None:
        if event_type == "token":
            self.current_agent_tokens.append(data.get("content", ""))
            return
        if event_type == "agent_start":
            self.current_agent = data.get("display_name") or data.get("agent", "")
            self.current_agent_tokens.clear()
        if event_type == "agent_done" and self.current_agent_tokens:
            self.final_output.append(
                f"**{self.current_agent}**\n{''.join(self.current_agent_tokens)}"
            )
            self.current_agent_tokens.clear()
        line = format_agent_event(event_type, data)
        if line:
            self.status_lines.append(line)
        if event_type in ("complete", "error"):
            self.done = True

    def status_snapshot(self) -> str:
        if not self.status_lines:
            return "Thinking..."
        tail = self.status_lines[-12:]  # keep the message short
        return "\n".join(tail)

    def final_text(self) -> str:
        return "\n\n".join(self.final_output) if self.final_output else "(no output)"


def _parse_allowlist(raw: str | None) -> set[int]:
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part and part.lstrip("-").isdigit():
            out.add(int(part))
    return out


class TelegramGateway:
    """Minimal Telegram bot driving the agent pipeline.

    Commands:
        /start           — greeting and usage.
        /ask <prompt>    — kick off the agent team; streams status updates.
        /status          — current provider + active model.

    Allowlist: the env var ``TELEGRAM_ALLOWED_USERS`` is a comma-separated
    list of Telegram user IDs. Empty allowlist = no one can use the bot.
    """

    STATUS_REFRESH_SECONDS = 2.0

    def __init__(
        self,
        token: str,
        allowed_user_ids: set[int] | None = None,
        *,
        bot_factory: Any = None,
        dispatcher_factory: Any = None,
    ):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self.token = token
        self.allowed_user_ids = allowed_user_ids or set()

        # Lazy aiogram import — only needed at start() time.
        self._bot_factory = bot_factory
        self._dispatcher_factory = dispatcher_factory
        self.bot = None
        self.dp = None

    # ── handler logic (testable without aiogram) ──────────────────────

    def is_authorized(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids

    async def run_pipeline(self, user_plan: str) -> SessionBuffer:
        """Run AgentTeam.run and accumulate events in a SessionBuffer."""
        from agent_team.agents.runner import AgentTeam
        buffer = SessionBuffer()
        emitter = CallbackEmitter(on_event=buffer.on_event)
        team = AgentTeam(emitter=emitter)
        try:
            await team.run(user_plan)
        except Exception as e:
            buffer.status_lines.append(f"⚠ pipeline error: {e!r}")
            buffer.done = True
        return buffer

    # ── aiogram wiring ────────────────────────────────────────────────

    async def start(self) -> None:  # pragma: no cover — requires aiogram + token
        try:
            from aiogram import Bot, Dispatcher, types
            from aiogram.filters import Command
        except ImportError as e:
            raise RuntimeError(
                "aiogram is not installed. Install with: "
                "pip install -e '.[gateway]'"
            ) from e

        bot_factory = self._bot_factory or Bot
        dispatcher_factory = self._dispatcher_factory or Dispatcher
        self.bot = bot_factory(self.token)
        self.dp = dispatcher_factory()

        @self.dp.message(Command("start"))
        async def _on_start(msg: types.Message):
            await self._handle_start(msg)

        @self.dp.message(Command("status"))
        async def _on_status(msg: types.Message):
            await self._handle_status(msg)

        @self.dp.message(Command("ask"))
        async def _on_ask(msg: types.Message):
            await self._handle_ask(msg)

        logger.info("Telegram gateway starting; %d user(s) allowlisted",
                    len(self.allowed_user_ids))
        await self.dp.start_polling(self.bot)

    # ── message handlers ──────────────────────────────────────────────

    async def _handle_start(self, msg) -> None:  # pragma: no cover — UI glue
        await msg.answer(
            "Hi — I'm your agent team. Use /ask <prompt> to kick off a run. "
            "Use /status to see the active provider and model."
        )

    async def _handle_status(self, msg) -> None:  # pragma: no cover — UI glue
        if not self.is_authorized(msg.from_user.id):
            await msg.answer("Not authorized.")
            return
        from agent_team.llm import get_provider
        from agent_team.llm.registry import get_active_provider_name
        provider_name = get_active_provider_name()
        try:
            model = get_provider().get_active_model()
        except Exception as e:
            model = f"(error: {e})"
        await msg.answer(f"provider: {provider_name}\nmodel: {model}")

    async def _handle_ask(self, msg) -> None:  # pragma: no cover — UI glue
        if not self.is_authorized(msg.from_user.id):
            await msg.answer("Not authorized.")
            return
        prompt = (msg.text or "").partition(" ")[2].strip()
        if not prompt:
            await msg.answer("Usage: /ask <prompt>")
            return

        status_msg = await msg.answer("Thinking...")
        buffer = SessionBuffer()
        emitter = CallbackEmitter(on_event=buffer.on_event)

        async def refresh_loop():
            last_snapshot = ""
            while not buffer.done:
                await asyncio.sleep(self.STATUS_REFRESH_SECONDS)
                snap = buffer.status_snapshot()
                if snap != last_snapshot:
                    try:
                        await status_msg.edit_text(snap[:TELEGRAM_MAX_LEN])
                        last_snapshot = snap
                    except Exception as e:
                        logger.debug("status refresh failed: %s", e)

        async def pipeline_task():
            from agent_team.agents.runner import AgentTeam
            try:
                team = AgentTeam(emitter=emitter)
                await team.run(prompt)
            except Exception as e:
                buffer.status_lines.append(f"⚠ pipeline error: {e!r}")
            finally:
                buffer.done = True

        await asyncio.gather(pipeline_task(), refresh_loop())

        final = buffer.final_text()
        escaped = escape_markdown_v2(final)
        for chunk in chunk_for_telegram(escaped):
            await msg.answer(chunk, parse_mode="MarkdownV2")


def gateway_from_env() -> TelegramGateway:
    """Construct a gateway from ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_ALLOWED_USERS``."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    allowed = _parse_allowlist(os.environ.get("TELEGRAM_ALLOWED_USERS"))
    return TelegramGateway(token, allowed)
