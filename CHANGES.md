# Change Log

---

## v2.2.0 — Distribution Ready (2026-03-22)

### Summary

Made the project ready for sharing via git or zip. Added one-command setup, `.gitignore`, and rewrote the README for the new structure.

### New Files

| File | Description |
|---|---|
| `setup.sh` | One-time setup script: installs deps, creates `~/bin` symlinks, adds to PATH, checks Ollama |
| `.gitignore` | Excludes runtime data, venv, caches, IDE files, secrets from version control |

### Changes

| File | What changed |
|---|---|
| `README.md` | Complete rewrite for v2.x structure: new quick start, project tree, CLI commands reference, sharing instructions |

### How to Share

**Git:** standard `git init && git add -A && git commit`. `.gitignore` handles exclusions.

**Zip:**
```bash
zip -r agent-team.zip . -x '.venv/*' '__pycache__/*' 'data/*' 'plans/*' '.idea/*' '.DS_Store'
```

**Recipient setup:** `./setup.sh` → `mat-agent-cli`

---

## v2.1.1 — Project Structure Reorganization (2026-03-22)

### Summary

Professional restructure of the entire repository. All source code now lives under `src/agent_team/`, shell scripts under `bin/`, and the Python package is properly named `agent_team` instead of `backend`.

### Structure Changes

```
BEFORE                          AFTER
───────────────────────         ───────────────────────────────
cli.py                    →     src/agent_team/cli/classic.py
interactive_cli.py        →     src/agent_team/cli/interactive.py
frontend_ui.py            →     src/agent_team/ui/gradio_app.py
frontend/index.html       →     src/agent_team/ui/static/index.html
backend/app.py            →     src/agent_team/server/app.py
backend/config.py         →     src/agent_team/config.py
backend/agents/*          →     src/agent_team/agents/*
backend/memory/*          →     src/agent_team/memory/*
backend/learning/*        →     src/agent_team/learning/*
backend/ollama/*          →     src/agent_team/ollama/*
backend/files/*           →     src/agent_team/files/*
backend/plans/*           →     src/agent_team/plans/*
backend/security/*        →     src/agent_team/security/*
backend/skills/*          →     src/agent_team/skills/*
mat-agent                 →     bin/mat-agent
mat-agent-cli             →     bin/mat-agent-cli
start.sh                  →     bin/start.sh
agent-team-plans/         →     plans/
requirements.txt          →     (removed, pyproject.toml is authoritative)
```

### Key Changes

| Area | Detail |
|---|---|
| Package rename | `backend.*` → `agent_team.*` across all ~50 import statements |
| Config path | `REPO_ROOT` adjusted for `src/agent_team/` depth (3 levels up) |
| Plans dir | `agent-team-plans/` → `plans/` |
| pyproject.toml | Added `[project.scripts]` entry points and `[tool.setuptools.packages.find]` |
| Shell scripts | All resolve repo root via `bin/..` pattern, export `PYTHONPATH` |
| uvicorn target | `backend.app:app` → `agent_team.server.app:app` |

### Entry Points

```bash
bin/mat-agent              # Launch backend + Gradio UI
bin/mat-agent-cli          # Interactive CLI (default)
bin/mat-agent-cli --classic  # Original CLI
```

---

## v2.1.0 — Interactive CLI with Rich UI (2026-03-22)

### Summary

Complete overhaul of the CLI experience. The new interactive CLI features a rich terminal UI with live status bar, token tracking, slash commands, model/mode switching, plan confirmation flow, and follow-up question support.

### New Files

| File | Description |
|---|---|
| `interactive_cli.py` | New interactive REPL CLI with rich formatting, prompt_toolkit input, slash commands |

### Modified Files

| File | What changed |
|---|---|
| `backend/ollama/client.py` | Added `TokenStats`, `SessionTokenTracker`, runtime model switching (`get_active_model`/`set_active_model`), token count capture from Ollama streaming responses |
| `backend/agents/runner.py` | Integrated `SessionTokenTracker` into `AgentTeam`, passes token stats through WebSocket, sends token summary on completion |
| `backend/app.py` | Added `POST /models/switch` endpoint for dynamic model switching, enhanced `GET /models` to include active model |
| `mat-agent-cli` | Updated to launch interactive CLI by default, `--classic` flag for original CLI |
| `pyproject.toml` | Added `rich`, `prompt_toolkit` dependencies |

### CLI Features

- **Startup banner** — ASCII art branding with version
- **Bottom status bar** — live display of connection status, LLM provider, model, mode, session token count
- **Slash commands:**
  - `/help` — command reference
  - `/model [name]` — switch or list models
  - `/mode <mode>` — switch between thinking/coding/brainstorming/architecture/execution
  - `/status` — connection & model info
  - `/tokens` — token usage table (per-agent breakdown with speed)
  - `/plan <task>` — plan-only mode
  - `/exec <task>` — plan + execute with directory selection
  - `/clear`, `/history`, `/exit`
- **Plan confirmation** — before execution, prompts for: plan only / execute in cwd / custom dir / cancel
- **Follow-up questions** — after each task, prompts for follow-up
- **Auto mode detection** — detects best mode from input keywords
- **Per-agent token stats** — prompt tokens, completion tokens, speed (tokens/sec)
- **Command history** — persistent across sessions (`~/.agent_team_history`)

### Backend Enhancements

- **Token tracking** — Ollama streaming responses now capture `prompt_eval_count`, `eval_count`, `eval_duration` from final chunk
- **Session token summary** — sent in the `complete` WebSocket message
- **Dynamic model switching** — `POST /models/switch` validates against Ollama's available models, applies at runtime without restart

### Usage

```bash
./mat-agent-cli              # new interactive CLI (default)
./mat-agent-cli --classic    # original CLI preserved
uv run python interactive_cli.py  # direct launch
```

---

## v2.0.0 — File Creation & Plan Storage Fixes

## Background

When a plan was executed via the CLI, the files described in the plan were never appearing on disk. Plans were also not being saved when using the WebSocket (CLI) path. This document covers every requirement raised and every change made to address them.

---

## Requirements

1. **After plan execution, files mentioned in the plan must appear on disk at the requested path.**
2. **Before execution, if a file/directory already exists, ask the user what to do (overwrite / skip / abort).**
3. **If a file/directory does not exist, create it (including parent directories) before the executor runs.**
4. **Plans must be saved to `agent-team-plans/` regardless of whether the WebSocket or HTTP path is used.**

---

## Files Changed

| File | What changed |
|---|---|
| `backend/main.py` | File writer, plan scaffolding, plan saving, execution path threading |
| `cli.py` | WebSocket start message — added `execution_path` field |

---

## `backend/main.py` — Detailed Changes

### 1. Added `_resolve_base_dir(execution_path)` *(new function)*

**Why:** Every file-writing function needs to know *where* on disk to write. When the user gives a path like `/Users/matthew/code/demo/calculation`, the PLANNER outputs paths that include the folder name (e.g. `calculation/main.py`). Using the execution path itself as base would double-nest files (`calculation/calculation/main.py`). The fix is to use the **parent** of the execution path as base, so `calculation/main.py` resolves to the correct location.

```python
def _resolve_base_dir(execution_path: str | None) -> Path:
    if execution_path:
        return Path(execution_path).expanduser().resolve().parent
    return REPO_ROOT
```

---

### 2. Added `extract_and_write_files(executor_output, execution_path, skip_existing)` *(new function)*

**Why:** CODE_EXECUTOR outputs file content wrapped in delimiters but nothing was actually writing those files to disk.

- Parses every `--- FILE: path ---` … `--- END FILE ---` block from CODE_EXECUTOR output.
- Creates parent directories automatically (`mkdir -p`).
- Respects `skip_existing=True` to leave pre-existing files untouched.
- Uses `_resolve_base_dir(execution_path)` so files land at the correct path.

---

### 3. Added `extract_plan_file_paths(planner_output)` *(new function)*

**Why:** To scaffold directories and files *before* CODE_EXECUTOR runs, we need to know which paths the plan refers to.

Extracts paths from two parts of PLANNER output:
- **Step lines**: `Step N: <desc> → path/to/file → Executor: CODE_EXECUTOR`
- **File tree lines**: lines with box-drawing characters (`│ ├ └`)

**Bug fixed:** The original regex also captured `N/A` (from `API contracts: N/A — frontend only`) because leading whitespace matched `\s+` and `N/A` contains a `/`. Fixed by requiring every path segment to be longer than 1 character.

---

### 4. Added `scaffold_plan_paths(planner_output, execution_path)` *(new function)*

**Why:** Files need to exist on disk before CODE_EXECUTOR fills them with content. This function reads the plan, creates any missing directories and empty placeholder files, and returns two lists: `created` and `existing`.

- Paths that **do not exist** → parent dirs + empty file created immediately.
- Paths that **already exist** → collected and returned to the caller to decide.

---

### 5. Updated `AgentTeam.__init__` — added `execution_path` parameter

```python
def __init__(self, ws: WebSocket, execution_path: str | None = None):
    self.execution_path = execution_path
```

---

### 6. Updated `AgentTeam.run_phase_3` — scaffold + plan saving + existing-file prompt

After PLANNER finishes (and any WAITING_FOR_USER loop resolves), `run_phase_3` now:

1. **Saves the plan** to `agent-team-plans/` via `save_plan_markdown`. Previously this only happened in the HTTP `/ask` endpoint — the WebSocket path never saved plans.
2. **Calls `scaffold_plan_paths`** to create missing directories and files.
3. **If existing files are found**, sends a `waiting_for_user` WebSocket message listing them and asking:
   - `overwrite` — CODE_EXECUTOR will replace them (default)
   - `skip` — leave existing files untouched
   - `abort` — stop the pipeline entirely
4. Stores the user's choice in `phase_outputs["_existing_file_choice"]` for phase 4 to honour.

---

### 7. Updated `AgentTeam.run_phase_4` — write files to correct path

```python
written = extract_and_write_files(
    executor_output,
    execution_path=self.execution_path,
    skip_existing=skip_existing,
)
```

Also sends a status message listing every file written.

---

### 8. Updated `AgentTeam.run_phase_5` (fix loops) — write fixed files to correct path

Both the QA_CHECKLIST and QA_REVIEWER fix loops now pass `execution_path` when re-running CODE_EXECUTOR:

```python
extract_and_write_files(fixed_output, execution_path=self.execution_path)
```

---

### 9. Updated `run_team_http` — scaffold + file writing with execution path

The HTTP pipeline (`/ask` endpoint) now:

- Passes `execution_path` into `scaffold_plan_paths` after PLANNER runs.
- Passes `execution_path` into `extract_and_write_files` after CODE_EXECUTOR runs.

```python
async def run_team_http(user_plan, mode, execution_path=None):
    ...
    scaffold_plan_paths(planner_output, execution_path=execution_path)
    ...
    extract_and_write_files(executor_output, execution_path=execution_path)
```

---

### 10. Updated `/ask` HTTP endpoint — passes execution_path to `run_team_http`

```python
phase_outputs = await run_team_http(user_plan_with_context, mode, execution_path=execution_path)
```

---

## `cli.py` — Detailed Changes

### 11. WebSocket start message — added `execution_path` field *(the root cause)*

**This was the root cause of files not appearing at the requested path.**

The CLI was baking the execution path into the plan text only, but never sending it as a separate field. The backend's `data.get("execution_path")` always returned `None`, so `_resolve_base_dir` always fell back to `REPO_ROOT`, writing files into the repo instead of the user's chosen directory.

**Before:**
```python
await ws.send(json.dumps({
    "type": "start",
    "content": full_plan,
    "mode": mode,
}))
```

**After:**
```python
await ws.send(json.dumps({
    "type": "start",
    "content": full_plan,
    "mode": mode,
    "execution_path": execution_path,   # ← added
}))
```

---

## End-to-End Flow (after all fixes)

```
User selects: custom directory → /Users/matthew/code/demo/calculation

CLI sends WebSocket message:
  { type: "start", content: "...", mode: "plan_and_execute",
    execution_path: "/Users/matthew/code/demo/calculation" }

Backend:
  AgentTeam(ws, execution_path="/Users/matthew/code/demo/calculation")

  Phase 3 (PLANNER):
    → save plan to agent-team-plans/
    → scaffold_plan_paths()
        _resolve_base_dir() → /Users/matthew/code/demo  (parent)
        "calculation/main.py" → mkdir + touch /Users/matthew/code/demo/calculation/main.py ✓
    → if existing files found → ask user: overwrite / skip / abort

  Phase 4 (CODE_EXECUTOR):
    → extract_and_write_files()
        base_dir = /Users/matthew/code/demo
        "calculation/main.py" → write to /Users/matthew/code/demo/calculation/main.py ✓
```

---

## Demo

A working example was verified at `/Users/matthew/code/demo/`:

- `demo/utils.py` — greet utility
- `demo/main.py` — entry point (`python3 main.py` → `Hello, World!`)

And at `/Users/matthew/code/demo/calculation/`:

- `calculation/calculator.py` — add / subtract / multiply / divide with `ZeroDivisionError` guard
- `calculation/main.py` — interactive REPL (`+`, `-`, `*`, `/`)
