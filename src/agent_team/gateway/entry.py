"""CLI entry: ``agent-team-gateway telegram``."""
from __future__ import annotations

import asyncio
import logging

import typer

from agent_team.llm.keys import load_keys_into_env

app = typer.Typer(add_completion=False, help="Run an agent-team messaging-platform gateway.")


@app.command()
def telegram() -> None:
    """Start the Telegram bot gateway (reads TELEGRAM_BOT_TOKEN from env)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_keys_into_env()
    from agent_team.gateway.telegram import gateway_from_env
    gateway = gateway_from_env()
    asyncio.run(gateway.start())


if __name__ == "__main__":
    app()
