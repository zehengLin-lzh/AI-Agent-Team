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

| Directory | Purpose |
|---|---|
| `bin/` | Entry point scripts (`mat-agent-cli`, `mat-agent`, `start.sh`) |
| `src/agent_team/` | Main Python package |
| `src/agent_team/cli/` | Rich interactive REPL + classic CLI |
| `src/agent_team/server/` | FastAPI backend (REST + WebSocket) |
| `src/agent_team/ui/` | Gradio web UI + standalone HTML |
| `src/agent_team/agents/` | Agent definitions, prompts, pipeline orchestrator, debate |
| `src/agent_team/llm/` | LLM provider abstraction (10 providers, runtime switching) |
| `src/agent_team/mcp/` | MCP integration (JSON-RPC 2.0 stdio client, tool registry) |
| `src/agent_team/memory/` | SQLite + vector storage, hybrid search, session indexing |
| `src/agent_team/learning/` | Self-learning (knowledge extraction, pattern recognition) |
| `src/agent_team/files/` | Code file writer + directory scaffolding |
| `src/agent_team/security/` | Command sandbox, input validation, workspace isolation |
| `src/agent_team/skills/` | Skill registry + SKILL.md loader |
| `skills/` | Skill definitions (thinking, coding, brainstorming, architecture, execution) |
| `data/` | Runtime data — SQLite, sessions (gitignored) |
| `plans/` | Generated plans (gitignored) |

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
| `/ask <question>` | Ask a single question (direct LLM, no agents) |
| `/chat` | Enter chat mode (direct LLM conversation, stateless) |
| `/scan [path]` | Scan a repo/directory (structure, functions, patterns) |
| `/plan <task>` | Plan-only mode (no execution) |
| `/exec <task>` | Plan + execute with directory selection |
| `/clear` | Clear screen |
| `/exit` | Exit |

---

## Agent Pipeline

Tasks are automatically classified by complexity (simple/medium/complex) and routed to the appropriate pipeline tier.

### Simple Tasks (4 LLM calls)
Legacy single-agent pipeline for quick, focused tasks.

| Phase | Agent | Role |
|---|---|---|
| 01 Intake | ORCHESTRATOR | Parse request, ask clarifying questions |
| 02 Plan | PLANNER | Execution plan, file tree |
| 03 Execute | EXECUTOR | Write code, generate output |
| 04 Verify | REVIEWER | Quality review, fix loops (up to 3) |

### Medium Tasks (7 LLM calls)
Dual orchestrator + reviewer with named agents.

| Phase | Agents | Role |
|---|---|---|
| 01 Intake | Lumusi → Ivor | Engineering manager + product manager discuss the task |
| 02 Think | Soren | Systems architect — deep analysis |
| 03 Plan | Atlas | Project lead — execution plan |
| 04 Execute | Kai | Senior implementer — write code |
| 05 Review | Quinn → Lena | QA lead + user advocate review quality |

### Complex Tasks (10+ LLM calls)
Full 12-agent pipeline with parallel groups and synthesis.

| Phase | Agents | Role |
|---|---|---|
| 01 Intake | Lumusi → Ivor | Dual orchestrator with discussion + synthesis |
| 02 Think | Soren → Mika | Architect + domain expert analysis |
| 02b Challenge | Vera | Devil's advocate — find gaps and risks |
| 03 Plan | Atlas → Nora | Project lead + dependency mapper |
| 04 Execute | Kai → Dev | Implementer + artifact builder |
| 04b Integrate | Sage | Integration specialist — merge outputs |
| 05 Review | Quinn → Lena | QA lead + user advocate |

Each multi-agent stage uses **sequential discussion**: Agent 1 produces output, Agent 2 reviews it and adds their perspective, then the lead agent synthesizes both into a single canonical output for downstream stages. Stages communicate via structured `---HANDOFF---` blocks with pass/blocked status.

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

## Version History & Accuracy Progress

Each version improved the agent team's output quality, measured against frontier LLM (Claude Opus) on identical complex tasks.

| Version | Score | Gap | Key Changes |
|---|---|---|---|
| **v2.0** | — | — | File creation, plan storage, execution path fixes |
| **v2.1** | — | — | Rich interactive CLI, token tracking, slash commands |
| **v2.3** | — | — | 10 LLM providers, API key management |
| **v2.5** | — | — | MCP integration, /ask & /chat commands |
| **v3.0** | 57/100 | 40 pts | Agent debate, session context, /scan, chain-of-thought, thinking model |
| **v3.0 r2** | 71/100 | 26 pts | Prompt optimization (specificity rules, stronger challenger) |
| **v3.0 r3** | 70/100 | 27 pts | RAG file reading (top 5 files), few-shot examples in THINKER |
| **v3.1** | 76/100 | 21 pts | PLANNER few-shot, REVIEWER rules, larger RAG budget (8000 chars) |
| **v4.0** | — | — | Pipeline fixes, plan reuse, 3-tier model routing, scan security, path auto-detect, file traceability |
| **v5.0** | 85/100 | 3 pts | Task complexity routing, color-coded file diffs, autonomous error learning |
| **v6.0** | — | — | 12-agent collaborative pipeline, sequential discussion, stage synthesis, structured handoffs |

For detailed changelogs per version, see [CHANGES.md](CHANGES.md).

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
zip -r agent-team.zip . -x '.venv/*' '__pycache__/*' 'data/*' 'plans/*' '.idea/*' '.DS_Store' 'mcp.json'
```

The `.gitignore` excludes: `.venv/`, `__pycache__/`, `data/` (runtime SQLite + sessions), `plans/` (generated plans), `mcp.json` (user-specific MCP config), `.idea/`, `.DS_Store`, and secrets.

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
