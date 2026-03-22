#!/bin/bash
# setup.sh — One-time setup for Agent Team
# Creates symlinks in ~/bin so you can run `mat-agent` and `mat-agent-cli` from anywhere.
#
# Usage:
#   chmod +x setup.sh && ./setup.sh

set -euo pipefail

_SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$_SOURCE" ]]; do _SOURCE="$(readlink "$_SOURCE")"; done
REPO_ROOT="$(cd "$(dirname "$_SOURCE")" && pwd)"

echo "🔧 Setting up Agent Team..."
echo "   Repo: $REPO_ROOT"
echo ""

# 1. Install Python dependencies
echo "📦 Installing dependencies..."
cd "$REPO_ROOT"
uv sync

# 2. Make scripts executable
chmod +x bin/mat-agent bin/mat-agent-cli bin/start.sh

# 3. Create ~/bin and symlinks
mkdir -p ~/bin
ln -sf "$REPO_ROOT/bin/mat-agent"     ~/bin/mat-agent
ln -sf "$REPO_ROOT/bin/mat-agent-cli" ~/bin/mat-agent-cli
echo "✓ Symlinked mat-agent → ~/bin/mat-agent"
echo "✓ Symlinked mat-agent-cli → ~/bin/mat-agent-cli"

# 4. Ensure ~/bin is on PATH
SHELL_RC=""
if [[ -n "${ZSH_VERSION:-}" ]] || [[ "$SHELL" == */zsh ]]; then
  SHELL_RC="$HOME/.zshrc"
elif [[ -n "${BASH_VERSION:-}" ]] || [[ "$SHELL" == */bash ]]; then
  SHELL_RC="$HOME/.bashrc"
fi

if [[ -n "$SHELL_RC" ]]; then
  if ! grep -q 'export PATH="$HOME/bin:$PATH"' "$SHELL_RC" 2>/dev/null; then
    echo '' >> "$SHELL_RC"
    echo '# Agent Team CLI' >> "$SHELL_RC"
    echo 'export PATH="$HOME/bin:$PATH"' >> "$SHELL_RC"
    echo "✓ Added ~/bin to PATH in $SHELL_RC"
  else
    echo "✓ ~/bin already on PATH"
  fi
fi

# 5. Check Ollama
echo ""
if command -v ollama &>/dev/null; then
  echo "✓ Ollama found"
  MODELS=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' || true)
  if [[ -n "$MODELS" ]]; then
    echo "  Available models:"
    echo "$MODELS" | while read -r m; do echo "    - $m"; done
  else
    echo "  ⚠️  No models pulled yet. Run: ollama pull qwen2.5-coder:7b"
  fi
else
  echo "⚠️  Ollama not installed. Install it:"
  echo "   brew install ollama        # macOS"
  echo "   curl -fsSL https://ollama.com/install.sh | sh  # Linux"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "   Start the full stack:    mat-agent"
echo "   Interactive CLI:         mat-agent-cli"
echo "   Classic CLI:             mat-agent-cli --classic"
echo ""
echo "   (Open a new terminal or run: source $SHELL_RC)"
