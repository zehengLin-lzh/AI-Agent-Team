#!/bin/bash
# start.sh — Start the Agent Team
# Run this from anywhere — resolves paths automatically

_SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$_SOURCE" ]]; do _SOURCE="$(readlink "$_SOURCE")"; done
REPO_ROOT="$(cd "$(dirname "$_SOURCE")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="$REPO_ROOT/src:${PYTHONPATH:-}"

echo "🤖 Starting Agent Team..."

# Check Ollama is running
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
  echo "⚠️  Ollama not running. Starting it..."
  ollama serve &
  sleep 2
fi

# Start FastAPI backend
echo "🚀 Starting backend on http://localhost:8000"
uv run uvicorn agent_team.server.app:app --reload --port 8000 &
BACKEND_PID=$!

# Start Gradio UI
echo "🌐 Starting Gradio UI on http://127.0.0.1:7860"
uv run python -m agent_team.ui.gradio_app &
FRONTEND_PID=$!

echo ""
echo "✅ Agent Team is running!"
echo "   Backend API:  http://localhost:8000"
echo "   Gradio UI:    http://127.0.0.1:7860"
echo "   Interactive:  bin/mat-agent-cli"
echo ""
echo "Press Ctrl+C to stop"

wait $BACKEND_PID $FRONTEND_PID
