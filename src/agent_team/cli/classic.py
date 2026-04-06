"""
CLI interface for the local Agent Team v2 — streams output live via WebSocket.

Usage examples:
  mat-agent-cli                                              # interactive stdin
  mat-agent-cli ask --plan "Build a todo API"
  mat-agent-cli ask --mode coding --plan "Build a todo API"
  mat-agent-cli ask --mode thinking --plan "Is P=NP?"
  mat-agent-cli ask --mode brainstorming --plan "New product ideas"
  mat-agent-cli recall "python debugging"
  mat-agent-cli stats
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
import typer
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedError as WsConnectionClosedError


app = typer.Typer(help="Agent Team v7.0 CLI — self-learning AI agent team")

BACKEND_URL = os.getenv("AGENT_TEAM_BACKEND_URL", "http://localhost:8000")

PHASE_LABELS = {
    "intake":  "Phase 01 · Intake",
    "think":   "Phase 02 · Think",
    "plan":    "Phase 03 · Plan",
    "execute": "Phase 04 · Execute",
    "verify":  "Phase 05 · Verify",
}

VALID_MODES = ["thinking", "coding", "brainstorming", "architecture", "execution"]


def _ws_url() -> str:
    return BACKEND_URL.replace("https://", "wss://").replace("http://", "ws://") + "/ws"


def _check_backend() -> None:
    """Exit with a clear message if the backend is not reachable."""
    try:
        with httpx.Client(timeout=5.0) as client:
            client.get(f"{BACKEND_URL}/health")
    except httpx.ConnectError:
        typer.echo(
            f"\nError: cannot reach backend at {BACKEND_URL}\n"
            "Start the backend first:\n"
            "  mat-agent          # full stack (backend + UI)\n"
            "  — or —\n"
            "  uv run uvicorn backend.app:app --reload --port 8000",
            err=True,
        )
        raise typer.Exit(code=1)


def _read_plan_from_stdin() -> str:
    typer.echo("Enter your request. Finish with Ctrl+D (Unix/macOS) or Ctrl+Z, Enter (Windows).")
    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


def _prompt_execution_path() -> tuple[Optional[str], str]:
    typer.echo("\nWhere should this be executed?")
    typer.echo("  1) current directory")
    typer.echo("  2) custom directory")
    typer.echo("  3) no execution (analysis/plan only)")
    choice = typer.prompt("Select an option [1/2/3]", default="3")

    if choice == "1":
        return os.getcwd(), "plan_and_execute"
    if choice == "2":
        path_str = typer.prompt("Enter directory path", default="").strip()
        if not path_str:
            return None, "plan_only"
        return path_str, "plan_and_execute"
    return None, "plan_only"


async def _stream(plan_text: str, mode: str, agent_mode: str, execution_path: Optional[str]) -> None:
    """Open a WebSocket to the backend and print tokens as they arrive."""
    if execution_path:
        full_plan = (
            f"{plan_text}\n\nExecution context:\n"
            f"- Requested path: {execution_path}\n"
            f"- Mode: {mode}"
        )
    else:
        full_plan = (
            f"{plan_text}\n\nExecution context:\n"
            f"- No execution path selected\n"
            f"- Mode: {mode}"
        )

    try:
        async with ws_connect(_ws_url(), max_size=10 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "type": "start",
                "content": full_plan,
                "mode": agent_mode,
                "execution_path": execution_path,
            }))

            current_agent = None
            agent_had_output = False
            _classic_buffers: dict[str, dict] = {}
            _classic_finished_q: list[str] = []

            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type")

                if t == "status":
                    phase = msg.get("phase", "")
                    label = PHASE_LABELS.get(phase, msg.get("message", ""))
                    typer.echo(f"\n{'─' * 56}")
                    typer.echo(f"  {label}")
                    typer.echo(f"{'─' * 56}")

                elif t == "agent_start":
                    agent_id = msg.get("agent", "")
                    if current_agent is None:
                        # First/only active agent — display live
                        current_agent = agent_id
                        agent_had_output = False
                        typer.echo(f"\n[{current_agent}]\n", nl=False)
                    else:
                        # Parallel agent — buffer it
                        _classic_buffers[agent_id] = {"tokens": "", "done": False, "stats": None}

                elif t == "token":
                    token_agent = msg.get("agent", current_agent)
                    token_content = msg.get("content", "")
                    if token_agent == current_agent:
                        sys.stdout.write(token_content)
                        sys.stdout.flush()
                        agent_had_output = True
                    elif token_agent in _classic_buffers:
                        _classic_buffers[token_agent]["tokens"] += token_content

                elif t == "agent_done":
                    done_agent = msg.get("agent", current_agent)
                    if done_agent == current_agent:
                        if agent_had_output:
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                        current_agent = None
                        # Flush agents that finished while active agent was displaying
                        for aid in list(_classic_finished_q):
                            buf = _classic_buffers.pop(aid, None)
                            if buf:
                                typer.echo(f"\n[{aid}]\n", nl=False)
                                sys.stdout.write(buf["tokens"])
                                if buf["tokens"]:
                                    sys.stdout.write("\n")
                                sys.stdout.flush()
                        _classic_finished_q.clear()
                        # Pick next running buffered agent
                        running = [a for a, b in _classic_buffers.items() if not b["done"]]
                        if running:
                            next_aid = running[0]
                            buf = _classic_buffers.pop(next_aid)
                            current_agent = next_aid
                            agent_had_output = bool(buf["tokens"])
                            typer.echo(f"\n[{current_agent}]\n", nl=False)
                            if buf["tokens"]:
                                sys.stdout.write(buf["tokens"])
                                sys.stdout.flush()
                    else:
                        if done_agent in _classic_buffers:
                            _classic_buffers[done_agent]["done"] = True
                            _classic_finished_q.append(done_agent)

                elif t == "memory_context":
                    results = msg.get("results", [])
                    if results:
                        typer.echo(f"\n{'─' * 56}")
                        typer.echo("  Memory: Found relevant past context")
                        typer.echo(f"{'─' * 56}")
                        for r in results[:3]:
                            preview = r.get("content", "")[:100]
                            typer.echo(f"  [{r.get('source', '?')}] {preview}...")

                elif t == "learning_complete":
                    patterns = msg.get("patterns_extracted", 0)
                    if patterns:
                        typer.echo(f"\n  Learned {patterns} new pattern(s) from this session.")

                elif t == "waiting_for_user":
                    typer.echo(f"\n\n  Agent needs clarification:")
                    typer.echo(f"   {msg.get('question', '')}")
                    user_reply = typer.prompt("\nYour answer")
                    await ws.send(json.dumps({"content": user_reply}))

                elif t == "complete":
                    typer.echo(f"\n{'═' * 56}")
                    typer.echo("  Done")
                    typer.echo(f"{'═' * 56}\n")
                    break

                elif t == "error":
                    typer.echo(f"\n  Error: {msg.get('content', 'Unknown error')}", err=True)
                    break

    except WsConnectionClosedError as e:
        typer.echo(f"\nConnection closed unexpectedly: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def ask(
    plan: Optional[str] = typer.Option(
        None, "--plan",
        help="Request text. If omitted, will be read from stdin.",
    ),
    plan_file: Optional[Path] = typer.Option(
        None, "--plan-file",
        exists=True, file_okay=True, dir_okay=False, readable=True,
        help="Path to a file containing the request.",
    ),
    execution_path: Optional[str] = typer.Option(
        None, "--execution-path",
        help="Directory where execution should be targeted.",
    ),
    plan_only: bool = typer.Option(
        False, "--plan-only",
        help="Stop after the Plan phase (no code execution).",
    ),
    mode: Optional[str] = typer.Option(
        None, "--mode",
        help="Agent mode: thinking, coding, brainstorming, architecture, execution",
    ),
):
    """Ask the Agent Team to process a task in the specified mode."""
    _check_backend()

    if plan_file is not None:
        plan_text = plan_file.read_text().strip()
    elif plan is not None:
        plan_text = plan.strip()
    else:
        plan_text = _read_plan_from_stdin()

    if not plan_text:
        typer.echo("No plan provided, exiting.")
        raise typer.Exit(code=1)

    # Determine agent mode
    agent_mode = mode or "coding"
    if agent_mode not in VALID_MODES:
        typer.echo(f"Invalid mode '{agent_mode}'. Valid modes: {', '.join(VALID_MODES)}")
        raise typer.Exit(code=1)

    # Auto-detect mode from keywords if not specified
    if mode is None:
        lower = plan_text.lower()
        if any(kw in lower for kw in ["brainstorm", "ideas", "creative", "ideate"]):
            agent_mode = "brainstorming"
        elif any(kw in lower for kw in ["architect", "design system", "system design", "infrastructure"]):
            agent_mode = "architecture"
        elif any(kw in lower for kw in ["analyze", "reason", "logic", "prove", "think through"]):
            agent_mode = "thinking"
        elif any(kw in lower for kw in ["run", "execute", "deploy", "start"]):
            agent_mode = "execution"

    typer.echo(f"\n  Mode: {agent_mode}")

    if plan_only:
        exec_path = None
        ws_mode = "plan_only"
    elif execution_path:
        exec_path = execution_path
        ws_mode = "plan_and_execute"
    elif agent_mode in ("coding", "execution"):
        exec_path, ws_mode = _prompt_execution_path()
    else:
        exec_path = None
        ws_mode = "plan_only"

    typer.echo(f"  Connecting to {BACKEND_URL} ...\n")
    asyncio.run(_stream(plan_text, ws_mode, agent_mode, exec_path))


@app.command()
def recall(
    query: str = typer.Argument(..., help="Search query for past sessions"),
    top_k: int = typer.Option(5, "--top-k", help="Number of results to return"),
):
    """Search memory for past knowledge and patterns."""
    try:
        from agent_team.memory.search import HybridSearch
        import asyncio

        async def _search():
            searcher = HybridSearch()
            results = await searcher.search(query, top_k=top_k)
            if not results:
                typer.echo("No relevant memories found.")
                return
            typer.echo(f"\nFound {len(results)} relevant memory chunk(s):\n")
            for i, r in enumerate(results, 1):
                typer.echo(f"{'─' * 56}")
                typer.echo(f"  #{i} [{r.source}] (score: {r.score:.3f})")
                typer.echo(f"{'─' * 56}")
                typer.echo(r.content[:500])
                typer.echo()

        asyncio.run(_search())
    except Exception as e:
        typer.echo(f"Error searching memory: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def stats():
    """Show learning statistics — sessions, memories, patterns."""
    try:
        from agent_team.learning.patterns import get_learning_stats
        s = get_learning_stats()
        typer.echo(f"\n{'═' * 40}")
        typer.echo("  Agent Team Learning Stats")
        typer.echo(f"{'═' * 40}")
        typer.echo(f"  Sessions completed:  {s['total_sessions']}")
        typer.echo(f"  Memory chunks:       {s['total_chunks']}")
        typer.echo(f"  Learned patterns:    {s['total_patterns']}")
        typer.echo(f"{'═' * 40}\n")
    except Exception as e:
        typer.echo(f"Error loading stats: {e}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
