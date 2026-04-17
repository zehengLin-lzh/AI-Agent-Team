#!/bin/bash
# start-gateway.sh — Run the Telegram gateway for Agent Team.
#
# Requires the optional `gateway` extra:
#   uv sync --extra gateway          (or)   pip install -e '.[gateway]'
#
# And the env vars TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS.

_SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$_SOURCE" ]]; do _SOURCE="$(readlink "$_SOURCE")"; done
REPO_ROOT="$(cd "$(dirname "$_SOURCE")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="$REPO_ROOT/src:${PYTHONPATH:-}"

PLATFORM="${1:-telegram}"

echo "🤖 Starting Agent Team gateway: $PLATFORM"

if command -v uv >/dev/null 2>&1; then
  exec uv run python -m agent_team.gateway.entry "$PLATFORM"
else
  exec python -m agent_team.gateway.entry "$PLATFORM"
fi
