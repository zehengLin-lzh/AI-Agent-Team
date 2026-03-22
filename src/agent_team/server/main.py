"""
Agent Team Backend — FastAPI + Ollama
Runs a 9-agent full-stack implementation team locally.
"""

import asyncio
import json
import os
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ─── Config ───────────────────────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434/api/chat"

# Best local model for code+reasoning. Change if you have less VRAM:
#   32b → needs ~20GB VRAM (best quality)
#   14b → needs ~10GB VRAM (good balance)
#   7b  → needs ~5GB  VRAM (fast, lighter)
MODEL = "qwen2.5-coder:7b"

MAX_FIX_LOOPS = 3

# Plan storage configuration
PLAN_DIR_ENV = "AGENT_TEAM_PLAN_DIR"
CONFIG_FILE = Path(__file__).resolve().parent.parent / "agent_team.config.json"
DEFAULT_PLAN_DIR = Path(__file__).resolve().parent.parent / "agent-team-plans"

app = FastAPI(title="Agent Team")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Agent Definitions ────────────────────────────────────────────────────────

class Phase(str, Enum):
    INTAKE    = "intake"
    THINK     = "think"
    PLAN      = "plan"
    EXECUTE   = "execute"
    VERIFY    = "verify"
    DONE      = "done"

AGENTS = {
    "ORCHESTRATOR": {
        "color": "#00ffaa",
        "phase": Phase.INTAKE,
        "system": """You are ORCHESTRATOR — the team lead of an AI agent team for full-stack technical implementation.

Your job when receiving a technical plan:
1. Restate the plan in your own structured words to confirm understanding
2. List ALL tasks as a numbered checklist
3. Identify: stack involved (frontend/backend/both), dependencies between tasks, any unknowns
4. If ANYTHING is unclear or ambiguous, you MUST ask the user before proceeding — never assume
5. Output a clean structured brief for the next agents

Output format (use exactly):
[ORCHESTRATOR]
Understanding: <1-sentence summary>

Tasks:
1. <task>
2. <task>
...

Stack: <what technologies>
Dependencies: <task relationships or "none">
Unknowns: <questions for user, or "none">

→ Routing to: THINKER_TECH

If there are unknowns, end with:
WAITING_FOR_USER: <your specific questions>
""",
    },

    "THINKER_TECH": {
        "color": "#a78bfa",
        "phase": Phase.THINK,
        "system": """You are THINKER_TECH — technical feasibility analyst for a full-stack AI agent team.

Given a structured task list, assess each task technically:
- Pick the right libraries, frameworks, patterns
- Frontend: React/Next.js architecture, state management, SSR vs CSR tradeoffs, component design
- Backend: API design (REST/tRPC), database schema, auth patterns, middleware
- Integration: how frontend/backend connect, data contracts, type sharing
- Flag anything that conflicts with modern best practices

Output format (use exactly):
[THINKER_TECH]
Feasibility: ✅ FEASIBLE / ⚠️ CONCERNS / ❌ BLOCKED

Technical approach:
1. <task> → <specific approach with library/pattern choices>
2. <task> → <specific approach>
...

Best practices notes: <anything to flag or "none">

→ Routing to: THINKER_RISK
""",
    },

    "THINKER_RISK": {
        "color": "#f87171",
        "phase": Phase.THINK,
        "system": """You are THINKER_RISK — risk hunter and edge case specialist for a full-stack AI agent team.

Analyze the plan and technical approach for everything that could go wrong:
- Security: XSS, CSRF, SQL injection, auth bypass, exposed secrets, IDOR
- Performance: N+1 queries, missing DB indexes, large bundle sizes, missing pagination
- Edge cases: empty states, concurrent requests, network failures, invalid inputs
- Error handling: unhandled promise rejections, missing try/catch, no user feedback on errors
- Data: race conditions, stale data, missing validation

Rate every risk HIGH/MED/LOW and give a concrete mitigation.

Output format (use exactly):
[THINKER_RISK]
Risks:
  [HIGH] <risk>: <mitigation>
  [MED]  <risk>: <mitigation>
  [LOW]  <risk>: <mitigation>

Edge cases to handle:
  - <case>
  - <case>

Security checklist:
  ✅/❌ Input validation
  ✅/❌ Auth on all protected routes
  ✅/❌ No secrets in frontend code
  ✅/❌ Error messages don't leak internals

→ Routing to: PLANNER
""",
    },

    "PLANNER": {
        "color": "#fbbf24",
        "phase": Phase.PLAN,
        "system": """You are PLANNER — architect and execution planner for a full-stack AI agent team.

Take all thinker analysis and produce the optimal implementation plan:
- Order tasks by dependency (what must be built first)
- Choose the simplest approach that fully solves the problem
- List EVERY file to be created or modified with its exact path
- Define API contracts (method, path, request body, response shape)
- Break into atomic steps — each step = one file or one clearly bounded change
- If any major architectural decision is uncertain, ask before proceeding

Output format (use exactly):
[PLANNER]
Execution plan:
  Step 1: <what> → <file path> → Executor: CODE_EXECUTOR
  Step 2: <what> → <file path> → Executor: CODE_EXECUTOR
  ...

File tree:
<tree of all files to create/modify>

API contracts:
  POST /api/<endpoint>
    Request: { field: type }
    Response: { field: type }
  ...
  (or "N/A — frontend only")

Confirmed approach: <one clear sentence>

→ Routing to: CODE_EXECUTOR
""",
    },

    "CODE_EXECUTOR": {
        "color": "#34d399",
        "phase": Phase.EXECUTE,
        "system": """You are CODE_EXECUTOR — the primary code writer for a full-stack AI agent team.

Implement exactly what PLANNER specified. Rules:
- Write ONE complete file at a time
- NO stubs, NO placeholders, NO TODOs (unless flagged with // FLAGGED: reason)
- TypeScript for all new files (unless project uses JS)
- Functional React components with proper prop types
- Handle loading states AND error states in every component
- Validate all API inputs server-side
- Use correct HTTP status codes (200/201/400/401/403/404/422/500)
- No console.log in production code — use proper error handling

File delimiter (use exactly):
--- FILE: path/to/file.ts ---
<complete file content>
--- END FILE ---

After each file:
Next: <what file comes next, or "all files complete">

→ Routing to: DOC_EXECUTOR (after all files done)
""",
    },

    "DOC_EXECUTOR": {
        "color": "#60a5fa",
        "phase": Phase.EXECUTE,
        "system": """You are DOC_EXECUTOR — documentation writer, runs after CODE_EXECUTOR.

For the code that was just written:
1. Write JSDoc/TSDoc comments for all exported functions and components
2. Document all API endpoints (copy from code, add descriptions)
3. List all required environment variables with descriptions
4. Note any setup steps needed

Output format:
[DOC_EXECUTOR]
--- DOCS: <feature name> ---
<documentation>
--- END DOCS ---

Environment variables needed:
  <VAR_NAME>=<description and example value>

Setup notes: <any special steps or "none">

→ Routing to: QA_CHECKLIST
""",
    },

    "QA_CHECKLIST": {
        "color": "#f472b6",
        "phase": Phase.VERIFY,
        "system": """You are QA_CHECKLIST — plan compliance verifier for a full-stack AI agent team.

Go back to the ORIGINAL user plan (from ORCHESTRATOR's task list).
Check every requirement against what CODE_EXECUTOR implemented.

For each requirement mark:
  ✅ Done — fully implemented
  ⚠️ Partial — implemented but missing something (explain what)
  ❌ Missing — not implemented at all

Output format (use exactly):
[QA_CHECKLIST]
Plan compliance:
  ✅ <requirement>
  ⚠️ <requirement> — missing: <what>
  ❌ <requirement> — reason: <why it was skipped>

Score: <X>/<total> requirements met

Result: PASS / NEEDS FIX

If NEEDS FIX:
FIX_REQUIRED:
  - <specific thing to fix>
  - <specific thing to fix>
→ Routing back to: CODE_EXECUTOR

If PASS:
→ Routing to: QA_TESTER
""",
    },

    "QA_TESTER": {
        "color": "#fb923c",
        "phase": Phase.VERIFY,
        "system": """You are QA_TESTER — test writer for a full-stack AI agent team.

Write test cases for all critical paths in the implemented code.
Use the most appropriate framework (Jest for unit/component, Playwright for e2e, pytest for Python).

Cover:
- Happy path (normal use)
- Error cases (invalid input, network failure, auth failure)
- Edge cases (empty data, max values, concurrent requests)
- Component rendering (if React)

Write COMPLETE test files, not just descriptions.

Output format:
[QA_TESTER]
--- TESTS: path/to/test/file ---
<complete test file>
--- END TESTS ---

Coverage summary:
  ✅ <what's covered>
  ⚠️ <what's harder to test automatically>

Bugs found during test writing: <list or "none">

→ Routing to: QA_REVIEWER
""",
    },

    "QA_REVIEWER": {
        "color": "#e879f9",
        "phase": Phase.VERIFY,
        "system": """You are QA_REVIEWER — code quality reviewer for a full-stack AI agent team.

Review all code written by CODE_EXECUTOR. Look for:
- Logic errors or off-by-one mistakes
- Missing null/undefined checks
- Hardcoded values that should be config/env vars
- Inconsistent patterns
- Performance issues (unnecessary re-renders, missing memoization, missing DB indexes)
- Security vulnerabilities (unvalidated input reaching DB, exposed sensitive data)

Rate each file:
  CLEAN — no issues
  MINOR ISSUES — small problems, list them
  NEEDS REVISION — significant problems, must fix

Output format (use exactly):
[QA_REVIEWER]
Code review:
  <filename>: CLEAN
  <filename>: MINOR ISSUES
    - <specific issue on line X>
  <filename>: NEEDS REVISION
    - <specific issue>

Overall: APPROVED / NEEDS WORK

If NEEDS WORK:
REVISION_REQUIRED:
  - <file>: <what to fix>
→ Routing back to: CODE_EXECUTOR

If APPROVED:
→ Routing to: QA_REPORTER
""",
    },

    "QA_REPORTER": {
        "color": "#94a3b8",
        "phase": Phase.VERIFY,
        "system": """You are QA_REPORTER — final delivery reporter. You always run last.

Compile the complete delivery report based on everything the team produced.

Output format (use exactly):
[QA_REPORTER]
═══════════════════════════════════════
         DELIVERY REPORT
═══════════════════════════════════════
Original plan: <summary>
Status: ✅ COMPLETE / ⚠️ PARTIAL / ❌ FAILED

Files delivered:
  📄 <path> — <what it does>
  📄 <path> — <what it does>

QA Results:
  Checklist:  ✅ PASS (<X>/<total> requirements)
  Tests:      ✅ WRITTEN / ⚠️ PARTIAL
  Review:     ✅ APPROVED / ⚠️ MINOR ISSUES

Accuracy: <X>%
Confidence: <HIGH/MEDIUM/LOW>

Known issues / future work:
  - <item or "none">

Environment setup needed:
  - <step or "none">
═══════════════════════════════════════
→ DELIVERY COMPLETE
""",
    },
}

# Execution order
PHASE_ORDER = [
    ["ORCHESTRATOR"],
    ["THINKER_TECH", "THINKER_RISK"],  # can run in parallel
    ["PLANNER"],
    ["CODE_EXECUTOR", "DOC_EXECUTOR"],  # parallel after executor done
    ["QA_CHECKLIST", "QA_TESTER", "QA_REVIEWER", "QA_REPORTER"],
]


# ─── Plan Storage Helpers ────────────────────────────────────────────────────────


def _get_plan_dir() -> Path:
    """Resolve the directory where plans should be stored.

    Priority:
    1) AGENT_TEAM_PLAN_DIR env var
    2) agent_team.config.json { "plan_dir": "..." } at repo root
    3) ./agent-team-plans relative to repo root
    """
    env_dir = os.getenv(PLAN_DIR_ENV)
    if env_dir:
        return Path(env_dir).expanduser()

    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            plan_dir = data.get("plan_dir")
            if plan_dir:
                return Path(plan_dir).expanduser()
        except Exception:
            # Fall back to default on any config parse error
            pass

    return DEFAULT_PLAN_DIR


def _slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower()).strip("-")
    return slug[:80] or "plan"


def save_plan_markdown(
    title: str,
    plan_text: str,
    execution_path: str | None,
    mode: str,
) -> Path:
    """Persist a planner output to a timestamped markdown file."""
    plan_dir = _get_plan_dir()
    plan_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify_title(title)
    filename = f"{timestamp}_{slug}.md"
    path = plan_dir / filename

    iso_ts = datetime.now().isoformat(timespec="seconds")
    header = [
        f"# {title}",
        "",
        f"- Timestamp: {iso_ts}",
        f"- Mode: {mode}",
        f'- Execution path: {execution_path or "(none)"}',
        "",
        "---",
        "",
    ]
    content = "\n".join(header) + plan_text
    path.write_text(content)
    return path

# ─── File Writer ──────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_base_dir(execution_path: str | None) -> Path:
    """Derive the file-writing base directory from an execution_path.

    When the user supplies execution_path (e.g. /code/demo), the PLANNER
    outputs paths like "demo/utils.py", so we write relative to the *parent*
    of execution_path (/code) so files land at /code/demo/utils.py.
    Without an execution_path we fall back to REPO_ROOT.
    """
    if execution_path:
        p = Path(execution_path).expanduser().resolve()
        return p.parent
    return REPO_ROOT


def extract_and_write_files(
    executor_output: str,
    execution_path: str | None = None,
    skip_existing: bool = False,
) -> list[Path]:
    """Parse CODE_EXECUTOR output and write embedded files to disk.

    Handles blocks of the form:
        --- FILE: path/to/file.ts ---
        <content>
        --- END FILE ---

    Creates missing parent directories automatically.
    If skip_existing=True, files that already exist on disk are left untouched.
    Returns list of Paths that were written.
    """
    base_dir = _resolve_base_dir(execution_path)

    written: list[Path] = []
    pattern = re.compile(
        r"---\s*FILE:\s*(.+?)\s*---\n(.*?)---\s*END FILE\s*---",
        re.DOTALL,
    )
    for match in pattern.finditer(executor_output):
        rel_path = match.group(1).strip().lstrip("/")
        content = match.group(2)
        target = base_dir / rel_path
        if skip_existing and target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(target)

    return written


# ─── Plan Scaffolding ─────────────────────────────────────────────────────────


def extract_plan_file_paths(planner_output: str) -> list[str]:
    """Extract file/directory paths from PLANNER output.

    Looks in two places:
    - Step lines: "Step N: <desc> → /path/to/file → Executor: ..."
    - File tree lines: any token that looks like a file/dir path
    """
    paths: list[str] = []

    # Step lines: capture the path between the first and second "→"
    for match in re.finditer(r"Step\s+\d+:.*?→\s*([^\s→,]+)\s*→", planner_output):
        candidate = match.group(1).strip()
        if "/" in candidate:
            paths.append(candidate)

    # File-tree lines: tokens that contain a "/" or look like filenames
    for match in re.finditer(r"[│├└─\s]+([\w.\-/]+(?:/[\w.\-/]+)+)", planner_output):
        candidate = match.group(1).strip()
        # Filter false positives (e.g. "N/A"): every segment must be >1 character
        if candidate and all(len(seg) > 1 for seg in candidate.split("/")):
            paths.append(candidate)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def scaffold_plan_paths(
    planner_output: str,
    execution_path: str | None = None,
) -> tuple[list[Path], list[Path]]:
    """Create missing directories and placeholder files from PLANNER output.

    For each path in the plan:
    - If it does not exist → create parent dirs and an empty file (or dir)
    - If it already exists → add to the `existing` list (caller decides)

    Returns (created, existing).
    """
    base_dir = _resolve_base_dir(execution_path)

    created: list[Path] = []
    existing: list[Path] = []

    for raw in extract_plan_file_paths(planner_output):
        clean = raw.lstrip("/")
        if not clean:
            continue
        target = base_dir / clean
        if target.exists():
            existing.append(target)
        else:
            # Treat as directory if path ends with "/" or has no extension
            if raw.endswith("/") or "." not in target.name:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            created.append(target)

    return created, existing


# ─── Ollama Client ────────────────────────────────────────────────────────────

async def stream_ollama(
    system_prompt: str,
    messages: list[dict],
    ws: WebSocket,
    agent_name: str,
) -> str:
    """Stream a response from Ollama, sending tokens over WebSocket."""
    full_response = ""

    payload = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
        "options": {
            "temperature": 0.3,      # lower = more consistent / precise
            "num_predict": 4096,
            "top_p": 0.9,
        },
    }

    await ws.send_json({
        "type": "agent_start",
        "agent": agent_name,
        "color": AGENTS[agent_name]["color"],
    })

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as response:
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            full_response += token
                            await ws.send_json({
                                "type": "token",
                                "agent": agent_name,
                                "content": token,
                            })
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        await ws.send_json({
            "type": "error",
            "agent": agent_name,
            "content": f"Ollama error: {str(e)}. Is Ollama running? Try: ollama serve",
        })

    await ws.send_json({"type": "agent_done", "agent": agent_name})
    return full_response

# ─── Agent Runner ─────────────────────────────────────────────────────────────

class AgentTeam:
    def __init__(self, ws: WebSocket, execution_path: str | None = None):
        self.ws = ws
        self.execution_path = execution_path
        self.conversation_history: list[dict] = []
        self.phase_outputs: dict[str, str] = {}
        self.fix_loop_count = 0
        self.original_plan = ""

    async def send_status(self, message: str, phase: str = ""):
        await self.ws.send_json({
            "type": "status",
            "message": message,
            "phase": phase,
        })

    def build_context_for_agent(self, agent_name: str) -> list[dict]:
        """Build the message history an agent should see."""
        msgs = []

        # Always include original user plan
        if self.original_plan:
            msgs.append({"role": "user", "content": self.original_plan})

        # Include relevant prior agent outputs
        context_agents = {
            "ORCHESTRATOR":  [],
            "THINKER_TECH":  ["ORCHESTRATOR"],
            "THINKER_RISK":  ["ORCHESTRATOR", "THINKER_TECH"],
            "PLANNER":       ["ORCHESTRATOR", "THINKER_TECH", "THINKER_RISK"],
            "CODE_EXECUTOR": ["ORCHESTRATOR", "PLANNER"],
            "DOC_EXECUTOR":  ["PLANNER", "CODE_EXECUTOR"],
            "QA_CHECKLIST":  ["ORCHESTRATOR", "CODE_EXECUTOR"],
            "QA_TESTER":     ["CODE_EXECUTOR"],
            "QA_REVIEWER":   ["CODE_EXECUTOR"],
            "QA_REPORTER":   ["ORCHESTRATOR", "QA_CHECKLIST", "QA_TESTER", "QA_REVIEWER"],
        }

        for prior_agent in context_agents.get(agent_name, []):
            if prior_agent in self.phase_outputs:
                msgs.append({
                    "role": "assistant",
                    "content": self.phase_outputs[prior_agent],
                })

        return msgs

    async def run_agent(self, agent_name: str) -> str:
        """Run a single agent and store its output."""
        agent_config = AGENTS[agent_name]
        messages = self.build_context_for_agent(agent_name)
        messages.append({
            "role": "user",
            "content": f"Please proceed as {agent_name}.",
        })

        output = await stream_ollama(
            system_prompt=agent_config["system"],
            messages=messages,
            ws=self.ws,
            agent_name=agent_name,
        )

        self.phase_outputs[agent_name] = output
        return output

    def needs_user_input(self, output: str) -> str | None:
        """Check if an agent is waiting for user clarification."""
        match = re.search(r"WAITING_FOR_USER:\s*(.+?)(?:\n\n|\Z)", output, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def needs_fix(self, output: str) -> list[str]:
        """Extract fix requirements from QA agents."""
        fixes = []
        match = re.search(r"FIX_REQUIRED:\n(.*?)(?:\n→|\Z)", output, re.DOTALL)
        if match:
            for line in match.group(1).strip().split("\n"):
                line = line.strip().lstrip("- ")
                if line:
                    fixes.append(line)
        match2 = re.search(r"REVISION_REQUIRED:\n(.*?)(?:\n→|\Z)", output, re.DOTALL)
        if match2:
            for line in match2.group(1).strip().split("\n"):
                line = line.strip().lstrip("- ")
                if line:
                    fixes.append(line)
        return fixes

    async def run_phase_1(self):
        """ORCHESTRATOR — parse plan, potentially ask user."""
        await self.send_status("Phase 1: Understanding your plan...", "intake")
        output = await self.run_agent("ORCHESTRATOR")

        question = self.needs_user_input(output)
        if question:
            await self.ws.send_json({
                "type": "waiting_for_user",
                "question": question,
                "agent": "ORCHESTRATOR",
            })
            # Wait for user response
            user_reply = await self.ws.receive_text()
            data = json.loads(user_reply)
            # Add clarification to history and re-run orchestrator
            self.phase_outputs["ORCHESTRATOR"] += f"\n\nUser clarification: {data['content']}"
            await self.send_status("Got it! Reprocessing with your clarification...", "intake")
            await self.run_agent("ORCHESTRATOR")

    async def run_phase_2(self):
        """THINKER_TECH + THINKER_RISK — run in parallel."""
        await self.send_status("Phase 2: Thinking — analyzing tech + risks in parallel...", "think")
        # Run both thinkers concurrently
        await asyncio.gather(
            self.run_agent("THINKER_TECH"),
            self.run_agent("THINKER_RISK"),
        )

    async def run_phase_3(self):
        """PLANNER — then scaffold directories/files from the plan."""
        await self.send_status("Phase 3: Planning optimal execution path...", "plan")
        output = await self.run_agent("PLANNER")

        question = self.needs_user_input(output)
        if question:
            await self.ws.send_json({
                "type": "waiting_for_user",
                "question": question,
                "agent": "PLANNER",
            })
            user_reply = await self.ws.receive_text()
            data = json.loads(user_reply)
            self.phase_outputs["PLANNER"] += f"\n\nUser decision: {data['content']}"
            await self.send_status("Updating plan with your decision...", "plan")
            output = await self.run_agent("PLANNER")

        # Save the plan to disk
        first_line = next((ln.strip() for ln in output.splitlines() if ln.strip()), "")
        title = first_line[:100] if first_line else "Agent Team Plan"
        save_plan_markdown(
            title=title,
            plan_text=output,
            execution_path=self.execution_path,
            mode=self._mode,
        )

        # Scaffold: create missing dirs/files; surface existing ones to the user
        await self.send_status("Checking plan paths on disk...", "plan")
        created, existing = scaffold_plan_paths(output, execution_path=self.execution_path)

        if existing:
            existing_list = "\n".join(
                f"  • {p.relative_to(REPO_ROOT)}" for p in existing
            )
            await self.ws.send_json({
                "type": "waiting_for_user",
                "question": (
                    f"The following files/directories already exist:\n{existing_list}\n\n"
                    "How would you like to handle them?\n"
                    "  overwrite — replace with generated content\n"
                    "  skip      — leave them as-is\n"
                    "  abort     — stop here"
                ),
                "agent": "PLANNER",
            })
            user_reply = await self.ws.receive_text()
            data = json.loads(user_reply)
            choice = data.get("content", "overwrite").strip().lower()

            if choice.startswith("abort"):
                await self.ws.send_json({
                    "type": "complete",
                    "message": "Aborted by user — existing files left untouched.",
                })
                raise WebSocketDisconnect()

            # Store choice so CODE_EXECUTOR / extract_and_write_files can respect it
            self.phase_outputs["_existing_file_choice"] = choice

            if choice.startswith("skip"):
                await self.send_status(
                    f"Skipping {len(existing)} existing file(s).", "plan"
                )
            else:
                await self.send_status(
                    f"Will overwrite {len(existing)} existing file(s).", "plan"
                )

        if created:
            await self.send_status(
                f"Scaffolded {len(created)} new path(s): "
                + ", ".join(str(p.relative_to(REPO_ROOT)) for p in created),
                "plan",
            )

    async def run_phase_4(self):
        """CODE_EXECUTOR then DOC_EXECUTOR."""
        await self.send_status("Phase 4: Writing code...", "execute")
        skip_existing = self.phase_outputs.get("_existing_file_choice", "overwrite").startswith("skip")
        executor_output = await self.run_agent("CODE_EXECUTOR")
        written = extract_and_write_files(
            executor_output,
            execution_path=self.execution_path,
            skip_existing=skip_existing,
        )
        if written:
            base = _resolve_base_dir(self.execution_path)
            await self.send_status(
                f"Wrote {len(written)} file(s) to disk: "
                + ", ".join(str(p.relative_to(base)) for p in written),
                "execute",
            )

        await self.send_status("Phase 4: Writing documentation...", "execute")
        await self.run_agent("DOC_EXECUTOR")

    async def run_phase_5(self) -> bool:
        """QA phase — returns True if passed, False if needs more fixes."""
        await self.send_status("Phase 5: Running QA verification...", "verify")

        # Checklist first
        checklist_output = await self.run_agent("QA_CHECKLIST")
        fixes = self.needs_fix(checklist_output)
        if fixes and self.fix_loop_count < MAX_FIX_LOOPS:
            self.fix_loop_count += 1
            await self.send_status(
                f"QA found issues — fix loop {self.fix_loop_count}/{MAX_FIX_LOOPS}...",
                "verify"
            )
            # Add fixes to executor context and re-run
            fix_context = "FIXES NEEDED:\n" + "\n".join(f"- {f}" for f in fixes)
            self.phase_outputs["CODE_EXECUTOR"] += f"\n\n{fix_context}"
            fixed_output = await self.run_agent("CODE_EXECUTOR")
            extract_and_write_files(fixed_output, execution_path=self.execution_path)
            return False  # Signal to loop

        # If checklist passed (or max loops hit), run remaining QA in parallel
        await self.send_status("Running tests and code review in parallel...", "verify")
        await asyncio.gather(
            self.run_agent("QA_TESTER"),
            self.run_agent("QA_REVIEWER"),
        )

        # Reviewer check
        reviewer_output = self.phase_outputs.get("QA_REVIEWER", "")
        review_fixes = self.needs_fix(reviewer_output)
        if review_fixes and self.fix_loop_count < MAX_FIX_LOOPS:
            self.fix_loop_count += 1
            await self.send_status(
                f"Reviewer found issues — fix loop {self.fix_loop_count}/{MAX_FIX_LOOPS}...",
                "verify"
            )
            fix_context = "CODE REVIEW FIXES:\n" + "\n".join(f"- {f}" for f in review_fixes)
            self.phase_outputs["CODE_EXECUTOR"] += f"\n\n{fix_context}"
            fixed_output = await self.run_agent("CODE_EXECUTOR")
            extract_and_write_files(fixed_output, execution_path=self.execution_path)
            return False

        # Final report
        await self.send_status("Generating delivery report...", "verify")
        await self.run_agent("QA_REPORTER")
        return True

    async def run(self, user_plan: str, mode: str = "plan_and_execute"):
        """Main entry point — run the full agent pipeline."""
        self.original_plan = user_plan
        self._mode = mode

        try:
            await self.run_phase_1()
            await self.run_phase_2()
            await self.run_phase_3()

            if mode == "plan_only":
                await self.ws.send_json({"type": "complete"})
                return

            await self.run_phase_4()

            # QA loop (max MAX_FIX_LOOPS)
            passed = False
            while not passed and self.fix_loop_count <= MAX_FIX_LOOPS:
                passed = await self.run_phase_5()
                if not passed and self.fix_loop_count >= MAX_FIX_LOOPS:
                    await self.send_status(
                        f"Max fix loops ({MAX_FIX_LOOPS}) reached — delivering with known issues.",
                        "verify"
                    )
                    await self.run_agent("QA_REPORTER")
                    break

            await self.ws.send_json({"type": "complete"})

        except WebSocketDisconnect:
            pass
        except Exception as e:
            await self.ws.send_json({
                "type": "error",
                "content": f"Team error: {str(e)}",
            })


# ─── HTTP Runner for /ask ────────────────────────────────────────────────────────


class AskMode(str, Enum):
    PLAN_ONLY = "plan_only"
    PLAN_AND_EXECUTE = "plan_and_execute"


class AskRequest(BaseModel):
    plan: str
    mode: AskMode | None = None
    execution_path: str | None = None


class AskResponse(BaseModel):
    title: str
    timestamp: str
    mode: AskMode
    execution_path: str | None
    plan_file_path: str
    phase_outputs: dict[str, str]


_HTTP_CONTEXT_AGENTS: dict[str, list[str]] = {
    "ORCHESTRATOR": [],
    "THINKER_TECH": ["ORCHESTRATOR"],
    "THINKER_RISK": ["ORCHESTRATOR", "THINKER_TECH"],
    "PLANNER": ["ORCHESTRATOR", "THINKER_TECH", "THINKER_RISK"],
    "CODE_EXECUTOR": ["ORCHESTRATOR", "PLANNER"],
    "DOC_EXECUTOR": ["PLANNER", "CODE_EXECUTOR"],
    "QA_CHECKLIST": ["ORCHESTRATOR", "CODE_EXECUTOR"],
    "QA_TESTER": ["CODE_EXECUTOR"],
    "QA_REVIEWER": ["CODE_EXECUTOR"],
    "QA_REPORTER": ["ORCHESTRATOR", "QA_CHECKLIST", "QA_TESTER", "QA_REVIEWER"],
}


async def _run_agent_http(
    agent_name: str,
    original_plan: str,
    phase_outputs: dict[str, str],
) -> str:
    """Run a single agent via Ollama, collecting the full response (no streaming)."""
    agent_config = AGENTS[agent_name]
    messages: list[dict[str, str]] = []

    if original_plan:
        messages.append({"role": "user", "content": original_plan})

    for prior_agent in _HTTP_CONTEXT_AGENTS.get(agent_name, []):
        if prior_agent in phase_outputs:
            messages.append({
                "role": "assistant",
                "content": phase_outputs[prior_agent],
            })

    messages.append({
        "role": "user",
        "content": f"Please proceed as {agent_name}.",
    })

    payload = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": agent_config["system"]},
            *messages,
        ],
        "options": {
            "temperature": 0.3,
            "num_predict": 4096,
            "top_p": 0.9,
        },
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(OLLAMA_URL, json=payload)
        r.raise_for_status()
        data = r.json()
        content = data.get("message", {}).get("content", "") or ""

    phase_outputs[agent_name] = content
    return content


async def run_team_http(
    user_plan: str,
    mode: AskMode,
    execution_path: str | None = None,
) -> dict[str, str]:
    """Run the agent team over HTTP, returning all phase outputs."""
    phase_outputs: dict[str, str] = {}

    # Phase 1: ORCHESTRATOR
    await _run_agent_http("ORCHESTRATOR", user_plan, phase_outputs)

    # Phase 2: THINKER_TECH + THINKER_RISK in parallel
    await asyncio.gather(
        _run_agent_http("THINKER_TECH", user_plan, phase_outputs),
        _run_agent_http("THINKER_RISK", user_plan, phase_outputs),
    )

    # Phase 3: PLANNER → scaffold missing paths, skip existing (no interactive channel)
    planner_output = await _run_agent_http("PLANNER", user_plan, phase_outputs)
    scaffold_plan_paths(planner_output, execution_path=execution_path)

    if mode == AskMode.PLAN_ONLY:
        return phase_outputs

    # Phase 4: CODE_EXECUTOR then DOC_EXECUTOR
    executor_output = await _run_agent_http("CODE_EXECUTOR", user_plan, phase_outputs)
    extract_and_write_files(executor_output, execution_path=execution_path)
    await _run_agent_http("DOC_EXECUTOR", user_plan, phase_outputs)

    # Phase 5: QA agents (simplified, no fix loops in HTTP mode)
    await _run_agent_http("QA_CHECKLIST", user_plan, phase_outputs)
    await asyncio.gather(
        _run_agent_http("QA_TESTER", user_plan, phase_outputs),
        _run_agent_http("QA_REVIEWER", user_plan, phase_outputs),
    )
    await _run_agent_http("QA_REPORTER", user_plan, phase_outputs)

    return phase_outputs

# ─── WebSocket Endpoint ───────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        # First message is always the user's plan
        raw = await websocket.receive_text()
        data = json.loads(raw)

        if data.get("type") == "start":
            team = AgentTeam(websocket, execution_path=data.get("execution_path"))
            await team.run(data["content"], mode=data.get("mode", "plan_and_execute"))

    except WebSocketDisconnect:
        pass


# ─── /ask HTTP Endpoint ─────────────────────────────────────────────────────────


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    """Run the agent team for a plan, optionally through full execution, and store the plan."""
    user_plan = request.plan.strip()
    if not user_plan:
        raise ValueError("plan must not be empty")

    # Determine mode: missing mode or empty execution path → plan_only
    mode = request.mode or AskMode.PLAN_ONLY
    execution_path = (request.execution_path or "").strip() or None
    if not execution_path and mode == AskMode.PLAN_AND_EXECUTE:
        mode = AskMode.PLAN_ONLY

    # Include execution context in the plan text so PLANNER can reason about it
    if execution_path:
        user_plan_with_context = (
            f"{user_plan}\n\nExecution context:\n- Requested path: {execution_path}\n"
            f"- Mode: {mode.value}"
        )
    else:
        user_plan_with_context = f"{user_plan}\n\nExecution context:\n- No execution path selected\n- Mode: {mode.value}"

    phase_outputs = await run_team_http(user_plan_with_context, mode, execution_path=execution_path)
    planner_output = phase_outputs.get("PLANNER", "") or user_plan

    # Derive a simple title from the first non-empty line
    first_line = next((ln.strip() for ln in planner_output.splitlines() if ln.strip()), "")
    title = first_line[:100] if first_line else "Agent Team Plan"

    plan_path = save_plan_markdown(
        title=title,
        plan_text=planner_output,
        execution_path=execution_path,
        mode=mode.value,
    )

    return AskResponse(
        title=title,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        mode=mode,
        execution_path=execution_path,
        plan_file_path=str(plan_path),
        phase_outputs=phase_outputs,
    )


# ─── Health + Status ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Check if Ollama is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://localhost:11434/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            has_model = any(MODEL.split(":")[0] in m for m in models)
            return {
                "status": "ok",
                "ollama": "connected",
                "model": MODEL,
                "model_available": has_model,
                "available_models": models,
            }
    except Exception as e:
        return {"status": "error", "ollama": f"not reachable: {e}"}

@app.get("/models")
async def list_models():
    """List available Ollama models."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://localhost:11434/api/tags")
            return r.json()
    except Exception as e:
        return {"error": str(e)}
