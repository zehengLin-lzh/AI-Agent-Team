#!/bin/bash
# Record a new CLI demo GIF
# Run this in a terminal: bash demo/record-demo.sh
#
# Steps after the CLI starts:
# 1. Wait for startup box to appear
# 2. Type: can you write a script to read json and convert to pandas dataframe
# 3. Let the agents run (Lumusi, Ivor, Soren, Atlas)
# 4. When asked to execute, press 1 (No)
# 5. Type: /quit
# 6. The GIF will be generated automatically

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CAST="$SCRIPT_DIR/cli-demo.cast"
GIF="$SCRIPT_DIR/cli-demo.gif"

echo "Recording CLI demo..."
echo "Press Ctrl+D or type /quit when done."
echo ""

asciinema rec "$CAST" \
  --cols 120 \
  --rows 35 \
  --overwrite \
  --idle-time-limit 3 \
  -c "python3 -m agent_team.cli.interactive"

echo ""
echo "Converting to GIF..."
agg "$CAST" "$GIF" \
  --theme monokai \
  --font-size 14 \
  --cols 120 \
  --rows 35

echo "Done! GIF saved to: $GIF"
echo "Size: $(du -h "$GIF" | cut -f1)"
