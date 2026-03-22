# Agent Team — Self-Learning Local AI Agent Team

A local-first AI agent team that thinks, plans, codes, and reviews — powered by Ollama. Supports 5 modes (thinking, coding, brainstorming, architecture, execution) with self-learning memory across sessions.

---

## Quick Start

```bash
# 1. Clone the repo
git clone <your-repo-url> "AI Agent Team"
cd "AI Agent Team"

# 2. Run setup (one-time)
chmod +x setup.sh && ./setup.sh

# 3. Pull a model
ollama pull qwen2.5-coder:7b

# 4. Launch
mat-agent-cli     # Interactive CLI
mat-agent         # Full stack (backend + web UI)
```

That's it. `mat-agent-cli` gives you the interactive terminal, `mat-agent` starts the backend + Gradio web UI.

---

## Project Structure

```
AI Agent Team/
├── bin/                              # Entry point scripts
│   ├── mat-agent                     #   Launch backend + web UI
│   ├── mat-agent-cli                 #   Interactive CLI (default entry point)
│   └── start.sh                      #   Full stack startup script
│
├── src/agent_team/                   # Main Python package
│   ├── config.py                     #   Global configuration
│   ├── cli/                          #   CLI interfaces
│   │   ├── interactive.py            #     Rich interactive REPL
│   │   └── classic.py                #     Original Typer CLI
│   ├── server/                       #   FastAPI backend
│   │   └── app.py                    #     REST + WebSocket API
│   ├── ui/                           #   Web interfaces
│   │   ├── gradio_app.py             #     Gradio web UI
│   │   └── static/index.html         #     Standalone web UI
│   ├── agents/                       #   Agent definitions & orchestration
│   │   ├── definitions.py            #     Agent roles, prompts, modes
│   │   ├── runner.py                 #     Pipeline orchestrator (WebSocket)
│   │   ├── http_runner.py            #     Pipeline orchestrator (HTTP)
│   │   └── context.py                #     Context builder for agents
│   ├── ollama/                       #   LLM client
│   │   └── client.py                 #     Streaming + token tracking
│   ├── memory/                       #   Memory system
│   │   ├── database.py               #     SQLite + vector storage
│   │   ├── embeddings.py             #     Embedding generation
│   │   ├── search.py                 #     Hybrid search (semantic + keyword)
│   │   └── indexer.py                #     Session indexing
│   ├── learning/                     #   Self-learning module
│   │   ├── extractor.py              #     Knowledge extraction
│   │   ├── patterns.py               #     Pattern recognition
│   │   └── feedback.py               #     Feedback storage
│   ├── files/                        #   File operations
│   │   ├── writer.py                 #     Code file writer
│   │   └── scaffolder.py             #     Directory scaffolding
│   ├── plans/                        #   Plan persistence
│   │   └── storage.py                #     Markdown plan storage
│   ├── security/                     #   Security layer
│   │   ├── sandbox.py                #     Command sandbox
│   │   ├── validator.py              #     Input validation
│   │   └── workspace.py              #     Workspace isolation
│   └── skills/                       #   Skill system
│       ├── registry.py               #     Skill registry
│       ├── loader.py                 #     SKILL.md loader
│       └── types.py                  #     Skill data types
│
├── skills/                           # Skill definitions (SKILL.md files)
│   ├── thinking/
│   ├── coding/
│   ├── brainstorming/
│   ├── architecture/
│   └── execution/
│
├── data/                             # Runtime data (gitignored)
├── plans/                            # Generated plans (gitignored)
├── setup.sh                          # One-time installation script
├── pyproject.toml                    # Dependencies & project config
├── CHANGES.md                        # Version history
└── .gitignore
```

---

## Prerequisites

| Dependency | Install |
|---|---|
| **uv** (Python package manager) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Ollama** (local LLM runtime) | `brew install ollama` (macOS) or [ollama.com](https://ollama.com/download) |
| **A model** | `ollama pull qwen2.5-coder:7b` |

---

## Installation

```bash
cd "AI Agent Team"
chmod +x setup.sh && ./setup.sh
```

`setup.sh` will:
1. Install Python dependencies via `uv sync`
2. Create `mat-agent` and `mat-agent-cli` symlinks in `~/bin`
3. Add `~/bin` to your PATH (if not already there)
4. Check Ollama installation and available models

After setup, open a new terminal (or `source ~/.zshrc`) and you're ready.

---

## Usage

### Interactive CLI (recommended)

```bash
mat-agent-cli
```

Features:
- Bottom status bar showing LLM, model, and mode
- Token usage tracking per agent
- Slash commands (`/help`, `/model`, `/mode`, `/tokens`, etc.)
- Auto mode detection from your input
- Plan confirmation before execution
- Follow-up questions after each task

### Full Stack (backend + web UI)

```bash
mat-agent
```

Starts:
- **Backend API** at `http://localhost:8000`
- **Gradio UI** at `http://127.0.0.1:7860`

### CLI Commands

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/llm [provider]` | Switch LLM provider (ollama / huggingface) or list providers |
| `/model [name]` | Switch model or list available models |
| `/mode <mode>` | Switch mode (thinking/coding/brainstorming/architecture/execution) |
| `/status` | Connection and model info |
| `/tokens` | Token usage for current session |
| `/plan <task>` | Plan-only mode (no execution) |
| `/exec <task>` | Plan + execute with directory selection |
| `/clear` | Clear screen |
| `/exit` | Exit |

---

## Agent Pipeline

| Phase | Agent | Role |
|---|---|---|
| 01 Intake | ORCHESTRATOR | Parse request, ask clarifying questions |
| 02 Think | THINKER | Deep analysis, feasibility, risk assessment |
| 03 Plan | PLANNER | Execution plan, file tree, API contracts |
| 04 Execute | EXECUTOR | Write code, generate documentation |
| 05 Verify | REVIEWER | Quality review, tests, fix loops (up to 3) |

---

## Modes

| Mode | Best for |
|---|---|
| `thinking` | Logical analysis, step-by-step reasoning, problem decomposition |
| `coding` | Code generation, implementation, bug fixes |
| `brainstorming` | Idea generation, creative exploration, SCAMPER |
| `architecture` | System design, database schemas, deployment planning |
| `execution` | Build + run code, with sandboxed command execution |

---

## LLM Providers

The system supports multiple LLM backends, switchable at runtime:

### Ollama (default — local, offline)

```bash
ollama pull qwen2.5-coder:7b
mat-agent-cli
# In CLI:
/llm ollama
/model qwen2.5-coder:14b
```

### HuggingFace (cloud API or local TGI)

```bash
# Set your HuggingFace token
export HF_TOKEN="hf_..."

# Start the backend, then in CLI:
/llm huggingface
/model Qwen/Qwen2.5-Coder-7B-Instruct
```

**Local TGI server** (for fully offline HF models):
```bash
# Run a TGI server (Docker)
docker run --gpus all -p 8080:80 \
  ghcr.io/huggingface/text-generation-inference:latest \
  --model-id Qwen/Qwen2.5-Coder-7B-Instruct

# Point Agent Team at it
export HF_API_URL="http://localhost:8080"
```

**Environment variables:**
| Variable | Description |
|---|---|
| `HF_TOKEN` | HuggingFace API token (required for cloud API) |
| `HF_API_URL` | Custom endpoint (default: HF Inference API) |
| `HF_MODEL` | Default HF model |

---

## Customization

**Switch provider at runtime:**
```
/llm huggingface
/llm ollama
```

**Switch model at runtime:**
```
/model qwen2.5-coder:14b
```

**Change default model** — edit `MODEL` in `src/agent_team/config.py`:
```python
MODEL = "qwen2.5-coder:14b"
```

**Store plans elsewhere:**
```bash
export AGENT_TEAM_PLAN_DIR="~/Documents/agent-plans"
```

---

## Recommended Models

### Ollama (local)

| Model | VRAM | Code Quality | Reasoning |
|---|---|---|---|
| `qwen2.5-coder:32b` | 20 GB | ★★★★★ | ★★★★ |
| `qwen2.5-coder:14b` | 10 GB | ★★★★ | ★★★ |
| `deepseek-r1:14b` | 10 GB | ★★★ | ★★★★★ |
| `qwen2.5-coder:7b` | 5 GB | ★★★ | ★★★ |

### HuggingFace

| Model | Type | Notes |
|---|---|---|
| `Qwen/Qwen2.5-Coder-7B-Instruct` | Code | Strong coding, good reasoning |
| `mistralai/Mistral-7B-Instruct-v0.3` | General | Balanced, fast |
| `meta-llama/Meta-Llama-3.1-8B-Instruct` | General | Strong reasoning |
| `microsoft/Phi-3.5-mini-instruct` | Compact | Small but capable |
| `deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct` | Code | Coding specialist |
| `bigcode/starcoder2-15b` | Code | Large code model |

---

## Sharing / Distribution

**Git:**
```bash
git init && git add -A && git commit -m "Initial commit"
```

**Zip:**
```bash
# .gitignore is respected — runtime data, venv, caches excluded
zip -r agent-team.zip . -x '.venv/*' '__pycache__/*' 'data/*' 'plans/*' '.idea/*' '.DS_Store'
```

The `.gitignore` excludes: `.venv/`, `__pycache__/`, `data/` (runtime SQLite + sessions), `plans/` (generated plans), `.idea/`, `.DS_Store`, and secrets.

Recipients just run:
```bash
unzip agent-team.zip -d "AI Agent Team"
cd "AI Agent Team"
./setup.sh
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `mat-agent: command not found` | Run `./setup.sh` or `source ~/.zshrc` |
| Backend won't start | Check Ollama: `ollama serve` |
| Model not found | Run `ollama pull qwen2.5-coder:7b` |
| Agents give short output | Switch to a larger model (`/model qwen2.5-coder:14b`) |
| Timeout errors | Increase timeout in `src/agent_team/config.py` |
