#!/bin/bash
# Record a new CLI demo GIF
# Run this in a terminal: bash demo/record-demo.sh
#
# What to show in the demo (v8.0):
# 1. Wait for startup — should show "openrouter" provider + model name
# 2. Type: what is the latest version of airflow
#    - Shows: "Task: MEDIUM / domain: research"
#    - Shows: "Task graph: 7 nodes (research domain)"
#    - Shows agents running with OpenRouter
# 3. When asked to execute, press 1 (No, just keep the plan)
# 4. Type: /quit
# 5. The GIF will be generated automatically
#
# Tips:
# - If you hit 429 rate limit, wait 1 min and re-record
# - Keep the demo under 60 seconds for reasonable GIF size
# - The idle-time-limit=3 compresses long agent waits

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CAST="$SCRIPT_DIR/cli-demo.cast"
GIF="$SCRIPT_DIR/cli-demo.gif"

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/src:${PYTHONPATH:-}"
export MAT_AGENT_CWD="$PWD"

echo "Recording CLI demo..."
echo "Press Ctrl+D or type /quit when done."
echo ""

asciinema rec "$CAST" \
  --cols 120 \
  --rows 35 \
  --overwrite \
  --idle-time-limit 3 \
  -c "uv run python -m agent_team.cli.interactive"

echo ""
echo "Converting to GIF..."
agg "$CAST" "$GIF" \
  --theme monokai \
  --font-size 14 \
  --cols 120 \
  --rows 35

echo "Done! GIF saved to: $GIF"
echo "Size: $(du -h "$GIF" | cut -f1)"
