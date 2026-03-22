# Mat Agent Team — Self-Learning Local AI Agent Team

A local-first AI agent team that thinks, plans, codes, and reviews — powered by Ollama. Supports 10 LLM providers, 5 modes, and self-learning memory across sessions.

## Demo

![CLI Demo](demo/cli-demo.gif)

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
│   ├── llm/                          #   LLM provider abstraction
│   │   ├── base.py                   #     Abstract provider interface
│   │   ├── registry.py               #     Provider registry & switching
│   │   ├── keys.py                   #     API key management (.env storage)
│   │   ├── openai_compat.py          #     OpenAI-compatible base class
│   │   ├── providers.py              #     8 frontier providers
│   │   ├── ollama_provider.py        #     Ollama provider
│   │   └── huggingface_provider.py   #     HuggingFace provider
│   ├── mcp/                          #   MCP integration
│   │   ├── config.py                 #     Server config (mcp.json)
│   │   ├── client.py                 #     Stdio client (JSON-RPC 2.0)
│   │   ├── registry.py               #     Server & tool registry
│   │   ├── triggers.py               #     Keyword trigger detection
│   │   └── tool_executor.py          #     Parse & execute tool calls
│   ├── ollama/                       #   Legacy Ollama client
│   │   └── client.py                 #     Direct Ollama streaming
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

| Dependency | Install | Required? |
|---|---|---|
| **uv** (Python package manager) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | Yes |
| **Ollama** (local LLM runtime) | `brew install ollama` (macOS) or [ollama.com](https://ollama.com/download) | Yes (for local mode) |
| **A model** | `ollama pull qwen2.5-coder:7b` | Yes (for local mode) |
| **API key** (for cloud providers) | Use `/key set <provider> <key>` in CLI | Optional |

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
| `/llm [provider]` | Switch LLM provider or list all 10 providers |
| `/key [set\|remove\|urls]` | Manage API keys (masked display, `.env` storage) |
| `/model [name]` | Switch model or list available models |
| `/mode <mode>` | Switch mode (thinking/coding/brainstorming/architecture/execution) |
| `/status` | Connection and model info |
| `/tokens` | Token usage for current session |
| `/mcp` | List MCP servers, status, and tools |
| `/mcp connect` | Connect to all configured MCP servers |
| `/mcp add <name>` | Add a new MCP server (interactive) |
| `/mcp search <query>` | Search for MCP servers by domain |
| `/skills` | List installed skills |
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

The system supports **10 LLM providers** (2 local + 8 frontier), all switchable at runtime via `/llm` and `/key` commands.

### Supported Providers

| Provider | Default Model | API Key Required |
|---|---|---|
| **Ollama** (default) | `qwen2.5-coder:7b` | No (local) |
| **HuggingFace** | `mistralai/Mistral-7B-Instruct-v0.3` | `HF_TOKEN` |
| **OpenAI** | `gpt-4o` | `OPENAI_API_KEY` |
| **Anthropic** | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` |
| **Google** | `gemini-2.5-flash` | `GOOGLE_API_KEY` |
| **Mistral** | `mistral-large-latest` | `MISTRAL_API_KEY` |
| **Groq** | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| **DeepSeek** | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| **Cohere** | `command-r-plus` | `COHERE_API_KEY` |
| **Together** | `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` | `TOGETHER_API_KEY` |

### API Key Management

API keys are stored locally in a `.env` file (gitignored) and masked on display.

```
/key                        # Show all key status (masked)
/key set openai sk-abc...   # Store a key
/key remove openai          # Remove a key
/key urls                   # Show signup URLs for all providers
```

You can also set keys via environment variables directly:
```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Switching Providers

```
/llm                    # List all providers
/llm anthropic          # Switch to Anthropic
/llm openai             # Switch to OpenAI
/llm ollama             # Switch back to local Ollama
/model gpt-4o-mini      # Switch model within active provider
```

### Ollama (default — local, offline)

```bash
ollama pull qwen2.5-coder:7b
mat-agent-cli
```

### HuggingFace (cloud API or local TGI)

```bash
/key set huggingface hf_...
/llm huggingface
```

**Local TGI server** (fully offline):
```bash
docker run --gpus all -p 8080:80 \
  ghcr.io/huggingface/text-generation-inference:latest \
  --model-id Qwen/Qwen2.5-Coder-7B-Instruct

export HF_API_URL="http://localhost:8080"
```

---

## MCP Integration (External Tools)

The agent team supports [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) for connecting external tools and services. MCP servers give agents access to databases, file systems, APIs, and more.

### Quick Start

```bash
# 1. Copy the example config
cp mcp.json.example mcp.json

# 2. Edit to enable the servers you need
# 3. In the CLI:
/mcp connect      # Connect to configured servers
/mcp tools        # See available tools
```

### Adding MCP Servers

**Interactive:**
```
/mcp add sqlite
  Type: stdio
  Command: uvx
  Arguments: mcp-server-sqlite --db-path data/db.sqlite
  Description: SQLite database management
  Triggers: database, sql, query
```

**Manual** — edit `mcp.json`:
```json
{
  "mcpServers": {
    "sqlite": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "data/db.sqlite"],
      "triggers": ["database", "sql", "query"],
      "enabled": true
    }
  }
}
```

### Keyword Triggers

When your request mentions domains like "database", "git", "web", etc., the CLI automatically suggests relevant MCP servers or skills. Search for servers:

```
/mcp search database
/mcp search git
/mcp search web
```

### Local vs Remote MCP

| MCP Type | Local LLM | Frontier LLM |
|---|---|---|
| **stdio** (local subprocess) | Supported | Supported |
| **SSE** (remote server) | Not supported | Supported |

Local LLMs (Ollama/HuggingFace) can only use local stdio MCP servers. For remote SSE servers, switch to a frontier LLM (`/llm openai`) or download the server source code to run locally.

---

## Customization

**Switch provider/model at runtime:**
```
/llm anthropic
/model claude-opus-4-20250514
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

### Frontier (cloud)

| Provider | Top Models |
|---|---|
| OpenAI | `gpt-4o`, `gpt-4.1`, `o3-mini` |
| Anthropic | `claude-sonnet-4-20250514`, `claude-opus-4-20250514` |
| Google | `gemini-2.5-pro`, `gemini-2.5-flash` |
| Mistral | `mistral-large-latest`, `codestral-latest` |
| Groq | `llama-3.3-70b-versatile` (fast inference) |
| DeepSeek | `deepseek-chat`, `deepseek-reasoner` |
| Together | `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` |

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
