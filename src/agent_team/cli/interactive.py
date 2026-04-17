#!/usr/bin/env python3
"""
Agent Team — Interactive CLI with rich UI.

A beautiful terminal interface for the local AI Agent Team.
Features: slash commands, token tracking, model switching, plan confirmation.

Usage:
  uv run python interactive_cli.py
  # or via the shortcut:
  ./mat-agent-cli
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style as PTStyle
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.columns import Columns
from rich.rule import Rule
from rich.spinner import Spinner
from rich.syntax import Syntax
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedError

from agent_team.agents.session import SessionContext
from agent_team.learning.feedback import detect_feedback, extract_and_store, MAX_AUTO_PER_SESSION

# ── Branding & Config ────────────────────────────────────────────────────────

APP_NAME = "Mat Agent Team"
APP_VERSION = "8.0.0"
BACKEND_URL = os.getenv("AGENT_TEAM_BACKEND_URL", "http://localhost:8000")
HISTORY_FILE = Path.home() / ".agent_team_history"

AGENT_ICONS = {
    # Legacy agents
    "ORCHESTRATOR": "\u2692",        # ⚒
    "THINKER": "\U0001f9e0",         # 🧠
    "CHALLENGER": "\u2694",          # ⚔
    "THINKER_REFINED": "\U0001f4a1", # 💡
    "PLANNER": "\U0001f4d0",         # 📐
    "EXECUTOR": "\u26a1",            # ⚡
    "REVIEWER": "\U0001f50d",        # 🔍
    # Named agents (12-agent pipeline)
    "ORCH_LUMUSI": "\u2692",         # ⚒ (Lumusi — Eng Manager)
    "ORCH_IVOR": "\U0001f4cb",      # 📋 (Ivor — Product Manager)
    "THINK_SOREN": "\U0001f9e0",    # 🧠 (Soren — Systems Architect)
    "THINK_MIKA": "\U0001f30d",     # 🌍 (Mika — Domain Expert)
    "THINK_VERA": "\u2694",         # ⚔ (Vera — Devil's Advocate)
    "PLAN_ATLAS": "\U0001f4d0",     # 📐 (Atlas — Project Lead)
    "PLAN_NORA": "\U0001f50d",      # 🔍 (Nora — Dependency Mapper)
    "EXEC_KAI": "\u26a1",           # ⚡ (Kai — Sr. Implementer)
    "EXEC_DEV": "\U0001f527",       # 🔧 (Dev — Artifact Builder)
    "EXEC_SAGE": "\U0001f517",      # 🔗 (Sage — Integration Specialist)
    "REV_QUINN": "\u2705",          # ✅ (Quinn — QA Lead)
    "REV_LENA": "\U0001f465",       # 👥 (Lena — User Advocate)
}

MODE_ICONS = {
    "thinking": "\U0001f9e0",       # 🧠
    "coding": "\U0001f4bb",         # 💻
    "brainstorming": "\U0001f4a1",  # 💡
    "architecture": "\U0001f3d7",   # 🏗
    "execution": "\u26a1",          # ⚡
}

VALID_MODES = ["thinking", "coding", "brainstorming", "architecture", "execution"]

# ── Rich theme ───────────────────────────────────────────────────────────────

custom_theme = Theme({
    # Legacy agents
    "agent.orchestrator": "bold green",
    "agent.thinker": "bold magenta",
    "agent.challenger": "bold red",
    "agent.thinker_refined": "bold bright_magenta",
    "agent.planner": "bold yellow",
    "agent.executor": "bold cyan",
    "agent.reviewer": "bold red",
    # Named agents
    "agent.orch_lumusi": "bold bright_magenta",
    "agent.orch_ivor": "bold blue",
    "agent.think_soren": "bold bright_blue",
    "agent.think_mika": "bold bright_cyan",
    "agent.think_vera": "bold cyan",
    "agent.plan_atlas": "bold yellow",
    "agent.plan_nora": "bold bright_yellow",
    "agent.exec_kai": "bold green",
    "agent.exec_dev": "bold bright_green",
    "agent.exec_sage": "bold cyan",
    "agent.rev_quinn": "bold red",
    "agent.rev_lena": "bold bright_magenta",
    "info": "dim cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "header": "bold white on blue",
    "muted": "dim white",
    "token": "dim green",
    "accent": "bold cyan",
})

console = Console(theme=custom_theme)

# prompt_toolkit style
pt_style = PTStyle.from_dict({
    "bottom-toolbar": "bg:#1a1a2e #e0e0e0",
    "bottom-toolbar.text": "#e0e0e0",
    "prompt": "bold #00d4aa",
})


# ── State ────────────────────────────────────────────────────────────────────

def _get_user_cwd() -> str:
    """Get the directory the user launched mat-agent-cli from."""
    return os.environ.get("MAT_AGENT_CWD", os.getcwd())


@dataclass
class CLIState:
    llm_provider: str = "ollama"
    available_providers: list[str] = field(default_factory=lambda: ["ollama", "huggingface"])
    model: str = "qwen2.5-coder:7b"
    mode: str = "coding"
    available_models: list[str] = field(default_factory=list)
    session_tokens: dict = field(default_factory=dict)
    total_session_tokens: int = 0
    conversation_count: int = 0
    backend_connected: bool = False
    mcp_registry: object | None = None  # MCPRegistry instance
    skill_registry: object | None = None  # SkillRegistry instance
    session: SessionContext = field(default_factory=SessionContext)
    user_cwd: str = field(default_factory=_get_user_cwd)  # Where the user launched from
    mcp_data_returned: bool = False  # Set True when MCP tools return data (skip execute prompt)
    auto_feedback_count: int = 0  # Auto-detected feedback count this session

state = CLIState()


# ── Backend Process Manager ──────────────────────────────────────────────────

_backend_process: subprocess.Popen | None = None


def _find_repo_root() -> Path:
    """Find the repo root (contains pyproject.toml)."""
    candidate = Path(__file__).resolve().parent.parent.parent.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    p = Path.cwd()
    while p != p.parent:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path.cwd()


async def _is_backend_running() -> bool:
    """Check if the backend is already running."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{BACKEND_URL}/health")
            return r.status_code == 200
    except Exception:
        return False


async def start_backend() -> bool:
    """Start the backend server as a subprocess if not already running."""
    global _backend_process

    if await _is_backend_running():
        return True

    repo_root = _find_repo_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root / 'src'}:{env.get('PYTHONPATH', '')}"

    console.print("[dim]Starting backend server...[/]", end=" ")

    try:
        _backend_process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn",
                "agent_team.server.app:app",
                "--port", "8000",
                "--log-level", "warning",
            ],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        # Wait for backend to be ready (up to 15 seconds)
        for _ in range(30):
            await asyncio.sleep(0.5)
            if await _is_backend_running():
                console.print("[success]\u2714 Backend started[/]")
                return True
            if _backend_process.poll() is not None:
                stderr = _backend_process.stderr.read().decode() if _backend_process.stderr else ""
                console.print("[error]\u2716 Backend failed to start[/]")
                if stderr:
                    console.print(f"[dim]{stderr[:300]}[/]")
                _backend_process = None
                return False

        console.print("[error]\u2716 Backend start timed out[/]")
        stop_backend()
        return False

    except Exception as e:
        console.print(f"[error]\u2716 Failed to start backend: {e}[/]")
        return False


def stop_backend():
    """Stop the backend subprocess if we started it."""
    global _backend_process
    if _backend_process is not None:
        try:
            _backend_process.terminate()
            try:
                _backend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _backend_process.kill()
                _backend_process.wait(timeout=3)
        except Exception:
            pass
        finally:
            _backend_process = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def ws_url() -> str:
    return BACKEND_URL.replace("https://", "wss://").replace("http://", "ws://") + "/ws"


async def check_backend() -> bool:
    """Check backend health and populate state.

    If the active provider is rate-limited (429), auto-switch to Ollama
    so the user isn't stuck with a broken session.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{BACKEND_URL}/health")
            data = r.json()
            status = data.get("status", "")
            error_str = data.get("error", "")

            # If provider is rate-limited, switch to Ollama
            if status != "ok" and ("429" in str(error_str) or "rate limit" in str(error_str).lower()):
                console.print("[yellow]Provider rate-limited → auto-switching to Ollama[/]")
                await client.post(f"{BACKEND_URL}/providers/switch", json={"provider": "ollama"})
                # providers/switch now auto-sets the correct default model
                r = await client.get(f"{BACKEND_URL}/health")
                data = r.json()
                status = data.get("status", "")

            state.backend_connected = status == "ok"
            if state.backend_connected:
                state.llm_provider = data.get("active_provider", state.llm_provider)
                state.model = data.get("model", state.model)
                # Fetch available models
                r2 = await client.get(f"{BACKEND_URL}/models")
                models_data = r2.json()
                state.available_models = [
                    m["name"] for m in models_data.get("models", [])
                ]
                active = models_data.get("active_model")
                if active:
                    state.model = active
                state.llm_provider = models_data.get("provider", state.llm_provider)
                # Fetch providers
                r3 = await client.get(f"{BACKEND_URL}/providers")
                prov_data = r3.json()
                state.available_providers = [
                    p["name"] for p in prov_data.get("providers", [])
                ]
            return state.backend_connected
    except Exception:
        state.backend_connected = False
        return False


async def switch_provider_remote(provider_name: str) -> dict:
    """Switch LLM provider on the backend."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(f"{BACKEND_URL}/providers/switch", json={"provider": provider_name})
        return r.json()


async def switch_model_remote(model_name: str) -> dict:
    """Switch model on the backend."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(f"{BACKEND_URL}/models/switch", json={"model": model_name})
        return r.json()


# ── UI Components ────────────────────────────────────────────────────────────

def render_banner():
    """Show startup banner (ASCII art only, no status)."""
    # Just store the banner — actual rendering happens in render_startup_box()
    pass


def render_startup_box():
    """Render a single unified startup box with banner + status, like Claude Code."""
    conn_icon = "[green]\u25cf[/]" if state.backend_connected else "[red]\u25cf[/]"
    conn_text = "Connected" if state.backend_connected else "Disconnected"
    cwd_display = state.user_cwd.replace(os.path.expanduser("~"), "~")

    # Build all content as one Text object — use no_wrap to prevent line wrapping
    content = Text(no_wrap=True, overflow="ellipsis")
    content.append(" __  __       _       _                  _   _____\n", style="bold cyan")
    content.append("|  \\/  | __ _| |_    / \\   __ _  ___ _ _| |_|_   _|__ __ _ _ __\n", style="bold cyan")
    content.append("| |\\/| |/ _` |  _|  / _ \\ / _` |/ -_) ' \\  _| | |/ -_) _` | '  \\\n", style="bold cyan")
    content.append("|_|  |_|\\__,_|\\__| /_/ \\_\\\\__, |\\___|_||_\\__| |_|\\___|\\__,_|_|_|_|\n", style="bold blue")
    content.append("                         |___/\n", style="bold magenta")

    # Status line appended inside the panel
    from rich.console import Group
    status_line = Text.from_markup(
        f" {conn_icon} {conn_text} [dim]\u00b7[/] "
        f"[accent]{state.llm_provider}[/] [dim]\u00b7[/] "
        f"[accent]{state.model}[/] [dim]\u00b7[/] "
        f"[accent]{state.mode}[/]\n"
        f" [dim]{cwd_display}[/]"
    )

    console.print(Panel(
        Group(content, status_line),
        title=f"[bold white]{APP_NAME} v{APP_VERSION}[/]",
        subtitle="[dim]Self-learning local AI agent team[/]",
        border_style="cyan",
        padding=(0, 2),
    ))


def render_status_bar():
    """Render the unified startup box (called after backend check)."""
    render_startup_box()


def render_help():
    """Show help panel with all slash commands."""
    help_table = Table(
        title="Available Commands",
        title_style="bold white",
        border_style="cyan",
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    help_table.add_column("Command", style="bold green", min_width=18)
    help_table.add_column("Description", style="white")

    commands = [
        ("/help", "Show this help menu"),
        ("/llm [provider]", "Switch LLM provider (e.g., /llm openai) or list all providers"),
        ("/key [provider] [key]", "Manage API keys — view status, set, or remove keys"),
        ("/key urls", "Show where to get API keys for each provider"),
        ("/model [name]", "Switch the active model (e.g., /model gpt-4o)"),
        ("/model list", "List all available models"),
        ("/mode <mode>", "Switch mode: thinking, coding, brainstorming, architecture, execution"),
        ("/status", "Show current connection status and model info"),
        ("/tokens", "Show token usage for the current session"),
        ("/clear", "Clear the screen"),
        ("/history", "Show conversation history summary"),
        ("(just type)", "Agent auto-routes: chat / question / task (with plan confirm)"),
        ("/mcp", "List MCP servers, tools, and status"),
        ("/mcp connect", "Connect to all configured MCP servers"),
        ("/mcp add <name>", "Add a new MCP server (interactive setup)"),
        ("/mcp search <query>", "Search for MCP servers by domain"),
        ("/mcp tools", "List all available MCP tools"),
        ("/scan [path]", "Scan a directory to understand its structure (stored in session)"),
        ("/cd <path>", "Change working directory"),
        ("/pwd", "Show current working directory"),
        ("/skills", "List installed skills"),
        ("/skills pending", "List candidate skills awaiting approval"),
        ("/skills review", "Interactively approve/reject pending candidates"),
        ("/skills show <name>", "Show full skill content"),
        ("/skills approve|reject <name>", "Promote or discard a candidate"),
        ("/skills delete <name>", "Remove an approved skill"),
        ("/remember <text>", "Store a rule or preference the agent should remember"),
        ("/forget <id_or_query>", "Deactivate a remembered rule by ID or search"),
        ("/learn-this", "Extract a rule from the last assistant message"),
        ("/feedback list", "Show all active feedback rules"),
        ("/exit, /quit", "Exit the CLI"),
    ]
    for cmd, desc in commands:
        help_table.add_row(cmd, desc)

    console.print()
    console.print(help_table)
    console.print()
    console.print("[dim]Tip: Just type your message and press Enter to start a conversation.[/]")
    console.print("[dim]     The agent team will auto-detect the best mode if not specified.[/]")
    console.print()


def render_token_summary(summary: dict | None = None):
    """Show token usage table."""
    if summary is None:
        summary = state.session_tokens

    if not summary:
        console.print("[muted]No token data yet. Start a conversation first.[/]")
        return

    table = Table(
        title="Token Usage",
        title_style="bold white",
        border_style="green",
        show_header=True,
        header_style="bold green",
    )
    table.add_column("Agent", style="bold")
    table.add_column("Prompt", justify="right", style="cyan")
    table.add_column("Completion", justify="right", style="yellow")
    table.add_column("Total", justify="right", style="bold white")
    table.add_column("Speed", justify="right", style="dim green")

    per_agent = summary.get("per_agent", {})
    for agent, stats in per_agent.items():
        icon = AGENT_ICONS.get(agent, "\u2022")
        speed = f"{stats.get('tokens_per_second', 0):.1f} t/s"
        table.add_row(
            f"{icon} {agent}",
            str(stats.get("prompt", 0)),
            str(stats.get("completion", 0)),
            str(stats.get("total", 0)),
            speed,
        )

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/]",
        str(summary.get("total_prompt", 0)),
        str(summary.get("total_completion", 0)),
        f"[bold green]{summary.get('total', 0)}[/]",
        "",
    )
    console.print()
    console.print(table)
    console.print()


def render_agent_header(agent_name: str, model: str = "", display_name: str = ""):
    """Show a styled agent header — Claude Code style with ╭─ prefix."""
    style_name = f"agent.{agent_name.lower()}"
    label = display_name or agent_name
    model_tag = f" [dim]({model})[/]" if model else ""
    console.print()
    console.print(f"[{style_name}]╭─ {label}[/]{model_tag}")


def render_phase_header(phase: str, label: str):
    """Show phase transition — slim divider line."""
    console.print()
    console.print(Rule(f"[dim]{label}[/]", style="dim"))


# ── Loading Spinner ──────────────────────────────────────────────────────────

class LoadingSpinner:
    """Animated dots spinner for waiting states. Non-blocking via asyncio."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "Thinking"):
        self.message = message
        self._task: asyncio.Task | None = None
        self._running = False
        self._frame_idx = 0

    async def _animate(self):
        """Animate the spinner on the current line."""
        import time
        while self._running:
            frame = self.FRAMES[self._frame_idx % len(self.FRAMES)]
            # Write spinner: \r to return to line start, then message
            sys.stdout.write(f"\r\033[2m{frame} {self.message}...\033[0m")
            sys.stdout.flush()
            self._frame_idx += 1
            await asyncio.sleep(0.08)

    def start(self, message: str | None = None):
        """Start the spinner animation."""
        if message:
            self.message = message
        self._running = True
        self._frame_idx = 0
        self._task = asyncio.ensure_future(self._animate())

    def stop(self, clear: bool = True):
        """Stop the spinner and clear the line."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        if clear:
            # Clear the spinner line
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


_EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".jsx": "jsx",
    ".tsx": "tsx", ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".html": "html", ".css": "css", ".json": "json", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "toml", ".sql": "sql", ".sh": "bash",
    ".md": "markdown", ".xml": "xml",
}
_MAX_DIFF_DISPLAY = 50


def _render_file_changes(console: Console, file_list, base: str):
    """Render color-coded file changes — green for new, yellow+diff for modified."""
    for f_info in file_list:
        # Support both old format (plain string) and new format (dict with diff)
        if isinstance(f_info, str):
            console.print(f"  [green]\u25b8[/] {f_info}")
            continue

        path = f_info.get("path", "")
        is_new = f_info.get("is_new", True)
        diff = f_info.get("diff")
        preview = f_info.get("preview")
        rel_path = path.replace(base + "/", "") if base and path.startswith(base) else path

        if is_new:
            # New file: green panel with preview
            if preview:
                ext = Path(path).suffix.lower()
                lang = _EXT_TO_LANG.get(ext, "text")
                try:
                    content_widget = Syntax(preview, lang, theme="monokai", line_numbers=True)
                except Exception:
                    content_widget = Text(preview, style="green")
            else:
                content_widget = Text(f"  {path}", style="green")
            console.print(Panel(
                content_widget,
                title=f"[bold green]NEW: {rel_path}[/]",
                border_style="green",
            ))
        else:
            # Modified file: yellow panel with diff
            if diff:
                diff_lines = diff.splitlines()
                truncated = False
                if len(diff_lines) > _MAX_DIFF_DISPLAY:
                    extra = len(diff_lines) - _MAX_DIFF_DISPLAY
                    diff_lines = diff_lines[:_MAX_DIFF_DISPLAY]
                    truncated = True
                display_diff = "\n".join(diff_lines)
                if truncated:
                    display_diff += f"\n... +{extra} more lines"
                try:
                    content_widget = Syntax(display_diff, "diff", theme="monokai")
                except Exception:
                    content_widget = Text(display_diff)
                console.print(Panel(
                    content_widget,
                    title=f"[bold yellow]MODIFIED: {rel_path}[/]",
                    border_style="yellow",
                ))
            else:
                # No diff available (identical content or read error)
                console.print(f"  [yellow]\u25b8[/] {rel_path} [dim](modified, no changes detected)[/]")


# ── Streaming Handler ────────────────────────────────────────────────────────

async def stream_conversation(
    plan_text: str,
    agent_mode: str,
    execution_path: str | None = None,
    plan_only: bool = False,
    reuse_plan: bool = False,
    phase_outputs: dict | None = None,
) -> tuple[dict | None, dict]:
    """Connect via WebSocket and stream the agent team output with rich formatting.
    Returns (token_summary, collected_phase_outputs)."""

    state.session.add_user_message(plan_text)

    if execution_path:
        full_plan = f"{plan_text}\n\nExecution context:\n- Requested path: {execution_path}\n- Mode: {agent_mode}"
    else:
        full_plan = f"{plan_text}\n\nExecution context:\n- No execution path selected\n- Mode: {agent_mode}"

    token_summary = None
    collected_outputs: dict[str, str] = {}

    try:
        async with ws_connect(
            ws_url(),
            max_size=10 * 1024 * 1024,
            ping_interval=30,    # Send ping every 30s
            ping_timeout=300,    # Allow 5 min for pong (user may be typing)
        ) as ws:
            start_msg: dict = {
                "type": "start",
                "content": full_plan,
                "mode": agent_mode,
                "plan_only": plan_only,
                "execution_path": execution_path,
                "session_context": state.session.get_context_summary(),
            }
            # A5: reuse prior plan outputs — skip re-thinking
            if reuse_plan and phase_outputs:
                start_msg["reuse_plan"] = True
                start_msg["phase_outputs"] = phase_outputs
            await ws.send(json.dumps(start_msg))

            current_buffer = ""
            current_agent = None
            at_line_start = True  # Track gutter state for │ prefix
            spinner = LoadingSpinner()
            first_token_received = False

            # Parallel streaming: buffer non-active agents, display one at a time
            # Keys: agent_id → {"tokens": str, "header": dict, "stats": dict|None, "done": bool}
            buffered_agents: dict[str, dict] = {}
            # Queue of agent_ids that finished while another was displaying
            finished_queue: list[str] = []

            def _render_buffered_output(buf_text: str) -> None:
                """Render a completed agent's buffered output with gutter."""
                nonlocal at_line_start
                at_line_start = True
                for ch in buf_text:
                    if at_line_start:
                        sys.stdout.write("\033[2m│\033[0m ")
                        at_line_start = False
                    sys.stdout.write(ch)
                    if ch == "\n":
                        at_line_start = True
                sys.stdout.flush()

            def _render_agent_footer(agent_name: str, token_stats: dict | None) -> None:
                """Render the ╰─ footer for an agent."""
                nonlocal at_line_start
                name = agent_name or ""
                if token_stats and token_stats.get("total_tokens", 0) > 0:
                    console.print(
                        f"[dim]╰─ {name} {token_stats['total_tokens']} tokens "
                        f"({token_stats.get('tokens_per_second', 0):.1f} t/s)[/]"
                    )
                else:
                    console.print(f"[dim]╰─ {name}[/]")
                at_line_start = True

            def _flush_finished_queue() -> None:
                """Render all agents that finished while another was displaying."""
                nonlocal current_agent, current_buffer, at_line_start, first_token_received
                while finished_queue:
                    aid = finished_queue.pop(0)
                    buf = buffered_agents.pop(aid, None)
                    if not buf:
                        continue
                    # Render header
                    h = buf["header"]
                    render_agent_header(h["agent"], h["model"], display_name=h.get("display_name", ""))
                    # Render buffered tokens
                    _render_buffered_output(buf["tokens"])
                    if buf["tokens"] and not buf["tokens"].endswith("\n"):
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                    # Render footer
                    _render_agent_footer(aid, buf.get("stats"))

            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type")

                if t == "status":
                    spinner.stop()
                    phase = msg.get("phase", "")
                    label = msg.get("message", phase)
                    render_phase_header(phase, label)
                    # Start spinner while waiting for next agent
                    spinner.start(label.split("(")[0].strip().rstrip("."))

                elif t == "agent_start":
                    spinner.stop()
                    agent_id = msg.get("agent", "")
                    model = msg.get("model", state.model)
                    display_name = msg.get("display_name", "")

                    if current_agent is None:
                        # No agent displaying — this one becomes active
                        current_agent = agent_id
                        current_buffer = ""
                        at_line_start = True
                        first_token_received = False
                        render_agent_header(agent_id, model, display_name=display_name)
                        short_name = display_name or agent_id
                        spinner.start(f"{short_name} thinking")
                    else:
                        # Another agent is already displaying — buffer this one
                        buffered_agents[agent_id] = {
                            "tokens": "",
                            "header": {"agent": agent_id, "model": model, "display_name": display_name},
                            "stats": None,
                            "done": False,
                        }

                elif t == "token":
                    token_agent = msg.get("agent", current_agent)
                    token_content = msg.get("content", "")

                    if token_agent == current_agent:
                        # Active agent — render live
                        if not first_token_received:
                            spinner.stop()
                            first_token_received = True
                        for ch in token_content:
                            if at_line_start:
                                sys.stdout.write("\033[2m│\033[0m ")
                                at_line_start = False
                            sys.stdout.write(ch)
                            if ch == "\n":
                                at_line_start = True
                        sys.stdout.flush()
                        current_buffer += token_content
                    else:
                        # Non-active agent — buffer silently
                        if token_agent in buffered_agents:
                            buffered_agents[token_agent]["tokens"] += token_content

                elif t == "agent_done":
                    spinner.stop()
                    done_agent = msg.get("agent", current_agent)
                    ts = msg.get("token_stats")

                    if done_agent == current_agent:
                        # Active agent finished — render its footer
                        if current_buffer and not current_buffer.endswith("\n"):
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                        _render_agent_footer(done_agent, ts)
                        current_agent = None
                        current_buffer = ""
                        first_token_received = False
                        # Flush any agents that finished while this one was displaying
                        _flush_finished_queue()
                        # If there are still-running buffered agents, pick one as new active
                        running = [aid for aid, b in buffered_agents.items() if not b["done"]]
                        if running:
                            next_aid = running[0]
                            buf = buffered_agents.pop(next_aid)
                            current_agent = next_aid
                            current_buffer = buf["tokens"]
                            first_token_received = bool(current_buffer)
                            at_line_start = True
                            h = buf["header"]
                            render_agent_header(h["agent"], h["model"], display_name=h.get("display_name", ""))
                            if current_buffer:
                                _render_buffered_output(current_buffer)
                            else:
                                short_name = h.get("display_name") or h["agent"]
                                spinner.start(f"{short_name} thinking")
                    else:
                        # Non-active agent finished — mark as done, enqueue
                        if done_agent in buffered_agents:
                            buffered_agents[done_agent]["stats"] = ts
                            buffered_agents[done_agent]["done"] = True
                            finished_queue.append(done_agent)
                        else:
                            # Edge case: agent finished without being tracked
                            _render_agent_footer(done_agent, ts)
                    at_line_start = True

                elif t == "memory_context":
                    results = msg.get("results", [])
                    if results:
                        console.print()
                        console.print("[dim]memory:[/]")
                        for r in results[:3]:
                            console.print(f"[dim]  \u2022 [{r.get('source', '?')}] {r.get('content', '')[:100]}[/]")

                elif t == "tool_results":
                    tools_executed = msg.get("tools_executed", [])
                    if tools_executed:
                        console.print()
                        for tr in tools_executed:
                            icon = "[green]✔[/]" if not tr.get("is_error") else "[red]✖[/]"
                            tool_name = tr["tool"]
                            result_text = tr.get("result", "")
                            args = tr.get("arguments", {})
                            args_short = ", ".join(f"{k}={v}" for k, v in args.items())[:60]
                            console.print(f"  {icon} [bold]{tool_name}[/]({args_short})")
                            # Show full result for query tools
                            if result_text and not tr.get("is_error"):
                                from rich.panel import Panel
                                from rich.syntax import Syntax
                                # If result looks like a markdown table, show it nicely
                                if "|" in result_text and "---" in result_text:
                                    console.print(Panel(
                                        result_text.strip(),
                                        title=f"[bold green]{tool_name}[/]",
                                        border_style="green",
                                        expand=False,
                                    ))
                                else:
                                    console.print(Panel(
                                        result_text.strip()[:2000],
                                        title=f"[bold green]{tool_name}[/]",
                                        border_style="dim",
                                        expand=False,
                                    ))
                            elif tr.get("is_error"):
                                console.print(f"    [red]{result_text[:200]}[/]")
                        # Track that MCP tools returned data
                        state.mcp_data_returned = True

                elif t == "agent_output":
                    agent = msg.get("agent", "")
                    content = msg.get("content", "")
                    state.session.add_agent_output(agent, content)
                    collected_outputs[agent] = content

                elif t in ("file_changes", "files_written"):
                    file_list = msg.get("files", [])
                    base = msg.get("base_dir", "")
                    if file_list:
                        _render_file_changes(console, file_list, base)

                elif t == "learning_complete":
                    patterns = msg.get("patterns_extracted", 0)
                    if patterns:
                        console.print(f"\n[success]\U0001f4a1 Learned {patterns} new pattern(s) from this session.[/]")

                elif t == "waiting_for_user":
                    spinner.stop()
                    question = msg.get("question", "")
                    console.print()
                    console.print(f"[bold yellow]? {question}[/]")
                    # Run input() in a thread so the event loop stays alive
                    # for WebSocket ping/pong (prevents keepalive timeout)
                    user_reply = (await asyncio.to_thread(input, "  > ")).strip()
                    await ws.send(json.dumps({"content": user_reply}))

                elif t == "complete":
                    spinner.stop()
                    token_summary = msg.get("token_summary")
                    model_used = msg.get("model", state.model)
                    total = token_summary.get("total", 0) if token_summary else 0
                    total_str = f", {total} tokens" if total else ""
                    console.print()

                    # ── Final Answer Panel ──────────────────────────────────
                    # Show the last meaningful agent output so the user doesn't
                    # have to scroll up through agent streams to find the answer.
                    # Priority: reviewer > planner > executor > thinker > any last output
                    _stage_priority = ["reviewer", "planner", "executor", "thinker", "orchestrator"]
                    final_answer = ""
                    # Try stage-level keys first (from task graph path)
                    for _stage in _stage_priority:
                        for _k, _v in collected_outputs.items():
                            if _stage in _k.lower() and _v.strip():
                                final_answer = _v.strip()
                                break
                        if final_answer:
                            break
                    # Fallback: last collected output regardless of name
                    if not final_answer and collected_outputs:
                        final_answer = list(collected_outputs.values())[-1].strip()
                    if final_answer:
                        import re as _re
                        # Strip internal protocol markers from display
                        display = _re.sub(r"---+\s*HANDOFF\s*---+.*?---+\s*END_HANDOFF\s*---+", "", final_answer, flags=_re.DOTALL).strip()
                        display = _re.sub(r"---+\s*TOOL_CALL:.*?---+\s*END\s*TOOL_CALL\s*---+", "", display, flags=_re.DOTALL).strip()
                        display = _re.sub(r"---+\s*TOOL_RESULT:.*?---+\s*END\s*TOOL_RESULT\s*---+", "", display, flags=_re.DOTALL).strip()
                        # Truncate very long answers
                        if len(display) > 3000:
                            display = display[:3000] + "\n... [truncated]"
                        if display:
                            from rich.panel import Panel
                            from rich.markdown import Markdown
                            console.print(Panel(
                                Markdown(display),
                                title="[bold green]Answer[/]",
                                border_style="green",
                                padding=(1, 2),
                            ))

                    console.print(Rule(
                        f"[bold green]done[/] [dim]({model_used}{total_str})[/]",
                        style="green",
                    ))
                    if token_summary:
                        state.session_tokens = token_summary
                        state.total_session_tokens += token_summary.get("total", 0)
                        render_token_summary(token_summary)
                    break

                elif t == "error":
                    spinner.stop()
                    console.print(f"\n[error]\u2716 Error: {msg.get('content', 'Unknown error')}[/]")
                    break

    except ConnectionClosedError as e:
        console.print(f"\n[error]Connection closed unexpectedly: {e}[/]")
    except ConnectionRefusedError:
        console.print("[error]Cannot connect to backend. Try /status to reconnect.[/]")

    return token_summary, collected_outputs


# ── Slash Command Handlers ───────────────────────────────────────────────────

async def handle_llm_command(args: str):
    """Handle /llm commands — list or switch LLM providers."""
    args = args.strip().lower()

    if not args:
        # List providers
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{BACKEND_URL}/providers")
                data = r.json()
                providers = data.get("providers", [])
        except Exception:
            providers = [
                {"name": "ollama", "active": state.llm_provider == "ollama", "model": "—", "status": "unknown"},
                {"name": "huggingface", "active": state.llm_provider == "huggingface", "model": "—", "status": "unknown"},
            ]

        table = Table(title="LLM Providers", border_style="cyan", header_style="bold cyan")
        table.add_column("Provider", style="bold")
        table.add_column("Model", style="white")
        table.add_column("Status", justify="center")
        table.add_column("Active", justify="center")

        for p in providers:
            status_icon = "[green]\u2714[/]" if p.get("status") == "ok" else "[red]\u2716[/]"
            active_icon = "[green]\u25cf[/]" if p.get("active") else ""
            table.add_row(p["name"], p.get("model", "—"), status_icon, active_icon)

        console.print()
        console.print(table)
        console.print()
        console.print("[dim]Usage: /llm <provider_name> to switch[/]")
        console.print("[dim]  Use /key to manage API keys for cloud providers[/]")
        return

    # Switch provider
    result = await switch_provider_remote(args)
    if result.get("status") == "ok":
        state.llm_provider = result.get("provider", args)
        state.model = result.get("model", state.model)
        console.print(f"[success]\u2714 Switched to: [bold]{state.llm_provider}[/] (model: {state.model})[/]")
        # Refresh model list for new provider
        await check_backend()
    else:
        error = result.get("error", "Unknown error")
        console.print(f"[error]\u2716 {error}[/]")


async def handle_key_command(args: str):
    """Handle /key commands — view, set, or remove API keys."""
    from agent_team.llm.keys import (
        get_key_status, save_key, remove_key, mask_key,
        PROVIDER_KEY_NAMES, PROVIDER_KEY_URLS,
    )

    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    subcmd_args = parts[1].strip() if len(parts) > 1 else ""

    if not subcmd:
        # Show all key statuses
        statuses = get_key_status()
        table = Table(
            title="API Key Status",
            border_style="cyan",
            header_style="bold cyan",
        )
        table.add_column("Provider", style="bold")
        table.add_column("Env Variable", style="dim")
        table.add_column("Key", style="white")
        table.add_column("Status", justify="center")

        for provider, info in statuses.items():
            if provider == "ollama":
                continue  # Ollama doesn't need a key
            status_icon = "[green]\u2714 Set[/]" if info["set"] else "[dim]\u2716 Not set[/]"
            table.add_row(
                provider,
                info["env_var"],
                info["masked"],
                status_icon,
            )

        console.print()
        console.print(table)
        console.print()
        console.print("[dim]Commands:[/]")
        console.print("[dim]  /key <provider> <api-key>   Set a key (e.g., /key openai sk-...)[/]")
        console.print("[dim]  /key remove <provider>      Remove a key[/]")
        console.print("[dim]  /key urls                   Show where to get keys[/]")
        return

    if subcmd == "urls":
        table = Table(title="Where to Get API Keys", border_style="green", header_style="bold green")
        table.add_column("Provider", style="bold")
        table.add_column("URL", style="cyan")
        for provider, url in PROVIDER_KEY_URLS.items():
            table.add_row(provider, url)
        console.print()
        console.print(table)
        return

    if subcmd == "remove":
        provider = subcmd_args.lower()
        if not provider:
            console.print("[warning]Usage: /key remove <provider>[/]")
            return
        if provider not in PROVIDER_KEY_NAMES:
            console.print(f"[error]Unknown provider: {provider}[/]")
            return
        removed = remove_key(provider)
        if removed:
            console.print(f"[success]\u2714 Removed key for {provider}[/]")
        else:
            console.print(f"[muted]No key found for {provider}[/]")
        return

    # Setting a key: /key <provider> <key-value>
    provider = subcmd.lower()
    key_value = subcmd_args

    if provider not in PROVIDER_KEY_NAMES:
        console.print(f"[error]Unknown provider: {provider}[/]")
        console.print(f"[dim]Available: {', '.join(p for p in PROVIDER_KEY_NAMES if p != 'ollama')}[/]")
        return

    if not key_value:
        # Prompt for key
        url = PROVIDER_KEY_URLS.get(provider, "")
        console.print(f"\n[bold]Set API key for {provider}[/]")
        if url:
            console.print(f"[dim]Get your key at: [cyan]{url}[/][/]")
        console.print("[dim]Paste your key below (it will be stored locally in .env):[/]")
        key_value = input("  API Key: ").strip()
        if not key_value:
            console.print("[muted]Cancelled.[/]")
            return

    save_key(provider, key_value)
    console.print(f"[success]\u2714 Key saved for {provider}: {mask_key(key_value)}[/]")
    console.print(f"[dim]  Stored in .env ({PROVIDER_KEY_NAMES[provider]})[/]")


async def handle_model_command(args: str):
    """Handle /model commands."""
    args = args.strip()

    if not args or args == "list":
        # List available models
        if not state.available_models:
            await check_backend()
        if state.available_models:
            table = Table(title="Available Models", border_style="cyan", header_style="bold cyan")
            table.add_column("Model", style="white")
            table.add_column("Active", justify="center")
            for m in state.available_models:
                is_active = "\u2714" if m == state.model or state.model in m else ""
                table.add_row(m, f"[green]{is_active}[/]" if is_active else "")
            console.print()
            console.print(table)
            console.print("\n[dim]Usage: /model <model_name> to switch[/]")
        else:
            console.print("[warning]No models found. Is Ollama running?[/]")
        return

    # Switch model
    result = await switch_model_remote(args)
    if result.get("status") == "ok":
        state.model = result["active_model"]
        console.print(f"[success]\u2714 Switched to model: [bold]{state.model}[/][/]")
    else:
        error = result.get("error", "Unknown error")
        console.print(f"[error]\u2716 {error}[/]")
        available = result.get("available")
        if available:
            console.print(f"[dim]Available: {', '.join(available)}[/]")


def handle_mode_command(args: str):
    """Handle /mode command."""
    args = args.strip().lower()

    if not args:
        table = Table(title="Available Modes", border_style="cyan", header_style="bold cyan")
        table.add_column("Mode", style="white")
        table.add_column("Description", style="dim")
        table.add_column("Active", justify="center")

        descriptions = {
            "thinking": "Deep logical analysis and step-by-step reasoning",
            "coding": "Code generation and implementation",
            "brainstorming": "Creative idea exploration and ideation",
            "architecture": "System design and technical planning",
            "execution": "Build, run, and deploy code",
        }
        for mode in VALID_MODES:
            icon = MODE_ICONS.get(mode, "")
            is_active = "\u2714" if mode == state.mode else ""
            table.add_row(
                f"{icon} {mode}",
                descriptions.get(mode, ""),
                f"[green]{is_active}[/]" if is_active else "",
            )
        console.print()
        console.print(table)
        console.print("\n[dim]Usage: /mode <mode_name> to switch[/]")
        return

    if args in VALID_MODES:
        state.mode = args
        icon = MODE_ICONS.get(args, "")
        console.print(f"[success]\u2714 Mode switched to: {icon} [bold]{args}[/][/]")
    else:
        console.print(f"[error]Invalid mode '{args}'. Valid: {', '.join(VALID_MODES)}[/]")


def _is_local_llm() -> bool:
    """Check if the active LLM provider is a local provider."""
    return state.llm_provider in ("ollama", "huggingface")


async def _ensure_mcp_registry():
    """Lazily initialize the MCP registry."""
    if state.mcp_registry is None:
        try:
            from agent_team.mcp.registry import MCPRegistry
            state.mcp_registry = MCPRegistry()
        except Exception:
            pass
    return state.mcp_registry


async def _ensure_skill_registry():
    """Lazily initialize the skill registry."""
    if state.skill_registry is None:
        try:
            from agent_team.skills.registry import SkillRegistry
            state.skill_registry = SkillRegistry()
        except Exception:
            pass
    return state.skill_registry


async def handle_mcp_command(args: str):
    """Handle /mcp commands.

    Subcommands:
      /mcp              — List all MCP servers and their status
      /mcp add <name>   — Add a new MCP server (interactive)
      /mcp remove <name> — Remove a server
      /mcp connect      — Connect to all enabled servers
      /mcp tools        — List all available tools
      /mcp search <q>   — Search for MCP servers online
      /mcp toggle <name> — Enable/disable a server
    """
    registry = await _ensure_mcp_registry()
    if registry is None:
        console.print("[error]MCP module not available.[/]")
        return

    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    subcmd_args = parts[1] if len(parts) > 1 else ""

    if not subcmd:
        # List all servers and status
        from agent_team.mcp.config import MCPConfig
        config = registry.config
        servers = config.list_servers()

        if not servers:
            console.print(Panel(
                "[dim]No MCP servers configured.\n\n"
                "Add a local server:\n"
                "  [bold]/mcp add <name>[/]\n\n"
                "Or create [bold]mcp.json[/] in the project root.\n\n"
                "Search for MCP servers:\n"
                "  [bold]/mcp search database[/][/]",
                title="[bold white]MCP Servers[/]",
                border_style="cyan",
            ))
            return

        table = Table(
            title="MCP Servers",
            title_style="bold white",
            border_style="cyan",
            header_style="bold cyan",
        )
        table.add_column("Server", style="bold")
        table.add_column("Type", style="dim")
        table.add_column("Status")
        table.add_column("Tools", justify="right")
        table.add_column("Description", style="dim")

        statuses = registry.get_statuses()
        status_map = {s.name: s for s in statuses}

        for server in servers:
            s = status_map.get(server.name)
            if not server.enabled:
                status_str = "[dim]\u25cb disabled[/]"
            elif s and s.connected:
                status_str = "[green]\u25cf connected[/]"
            elif s and s.error:
                status_str = f"[red]\u25cf {s.error[:30]}[/]"
            else:
                status_str = "[yellow]\u25cb not connected[/]"

            tool_count = len(s.tools) if s else 0
            type_str = "[cyan]stdio[/]" if server.type == "stdio" else "[yellow]sse[/]"

            table.add_row(
                server.name,
                type_str,
                status_str,
                str(tool_count) if tool_count else "-",
                server.description[:40] or "-",
            )

        console.print()
        console.print(table)
        console.print("\n[dim]Commands: /mcp connect | /mcp tools | /mcp add <name> | /mcp remove <name>[/]")
        return

    if subcmd == "connect":
        console.print("[dim]Connecting to MCP servers...[/]")
        statuses = await registry.connect_all()
        for s in statuses:
            if s.connected:
                console.print(f"  [green]\u2714 {s.name}[/] — {len(s.tools)} tool(s)")
            elif not s.enabled:
                console.print(f"  [dim]\u25cb {s.name} (disabled)[/]")
            else:
                console.print(f"  [red]\u2716 {s.name}[/] — {s.error or 'failed'}")
        return

    if subcmd == "tools":
        tools = registry.get_all_tools()
        if not tools:
            console.print("[warning]No tools available. Run /mcp connect first.[/]")
            return

        table = Table(
            title="MCP Tools",
            title_style="bold white",
            border_style="green",
            header_style="bold green",
        )
        table.add_column("Tool", style="bold cyan")
        table.add_column("Server", style="dim")
        table.add_column("Description", style="white")

        for tool in tools:
            table.add_row(tool.name, tool.server_name, tool.description[:60] or "-")

        console.print()
        console.print(table)
        return

    if subcmd == "add":
        name = subcmd_args.strip()
        if not name:
            console.print("[warning]Usage: /mcp add <name>[/]")
            return

        console.print(f"\n[bold]Adding MCP server: [cyan]{name}[/][/]\n")

        # Interactive setup
        server_type = input("  Type (stdio/sse) [stdio]: ").strip() or "stdio"

        if server_type == "sse":
            if _is_local_llm():
                console.print("[warning]Remote SSE servers are not fully supported with local LLMs.[/]")
                console.print("[dim]Local LLMs cannot reliably decompose requests for remote tool calls.[/]")
                console.print("[dim]Option 1: Switch to a frontier LLM (/llm openai)[/]")
                console.print("[dim]Option 2: Download the MCP server source code and run it locally.[/]")
                choice = input("  Continue anyway? (y/n) [n]: ").strip().lower()
                if choice != "y":
                    console.print("[dim]Cancelled.[/]")
                    return

            url = input("  Server URL: ").strip()
            if not url:
                console.print("[error]URL required for SSE servers.[/]")
                return

            from agent_team.mcp.config import MCPServerDef
            server_def = MCPServerDef(
                name=name, type="sse", url=url,
                description=input("  Description: ").strip(),
                triggers=[t.strip() for t in input("  Trigger keywords (comma-separated): ").split(",") if t.strip()],
            )
        else:
            command = input("  Command (e.g., npx, uvx, python): ").strip()
            if not command:
                console.print("[error]Command required for stdio servers.[/]")
                return

            args_str = input("  Arguments (space-separated): ").strip()
            args_list = args_str.split() if args_str else []

            from agent_team.mcp.config import MCPServerDef
            server_def = MCPServerDef(
                name=name, type="stdio",
                command=command, args=args_list,
                description=input("  Description: ").strip(),
                triggers=[t.strip() for t in input("  Trigger keywords (comma-separated): ").split(",") if t.strip()],
            )

        registry.config.add_server(server_def)
        console.print(f"\n[success]\u2714 Server '{name}' added to mcp.json[/]")
        console.print("[dim]Run /mcp connect to connect.[/]")
        return

    if subcmd == "remove":
        name = subcmd_args.strip()
        if not name:
            console.print("[warning]Usage: /mcp remove <name>[/]")
            return
        if registry.config.remove_server(name):
            await registry.disconnect_server(name)
            console.print(f"[success]\u2714 Server '{name}' removed.[/]")
        else:
            console.print(f"[error]Server '{name}' not found.[/]")
        return

    if subcmd == "toggle":
        name = subcmd_args.strip()
        if not name:
            console.print("[warning]Usage: /mcp toggle <name>[/]")
            return
        result = registry.config.toggle_server(name)
        if result is None:
            console.print(f"[error]Server '{name}' not found.[/]")
        elif result:
            console.print(f"[success]\u2714 Server '{name}' enabled.[/]")
        else:
            console.print(f"[dim]\u25cb Server '{name}' disabled.[/]")
            await registry.disconnect_server(name)
        return

    if subcmd == "search":
        query = subcmd_args.strip()
        if not query:
            console.print("[warning]Usage: /mcp search <query> (e.g., /mcp search database)[/]")
            return

        console.print(f"[dim]Searching for MCP servers related to '{query}'...[/]")
        console.print()

        # Show well-known MCP servers that match
        known_servers = {
            "database": [
                ("sqlite", "uvx mcp-server-sqlite --db-path data/db.sqlite", "SQLite database management"),
                ("postgres", "npx -y @modelcontextprotocol/server-postgres", "PostgreSQL database access"),
            ],
            "filesystem": [
                ("filesystem", "npx -y @modelcontextprotocol/server-filesystem /path", "File system access"),
            ],
            "git": [
                ("git", "uvx mcp-server-git", "Git repository operations"),
                ("github", "npx -y @modelcontextprotocol/server-github", "GitHub API integration"),
            ],
            "web": [
                ("fetch", "uvx mcp-server-fetch", "Fetch and parse web pages"),
                ("brave-search", "npx -y @modelcontextprotocol/server-brave-search", "Web search via Brave"),
            ],
            "memory": [
                ("memory", "npx -y @modelcontextprotocol/server-memory", "Knowledge graph memory"),
            ],
            "docker": [
                ("docker", "npx -y @modelcontextprotocol/server-docker", "Docker container management"),
            ],
            "slack": [
                ("slack", "npx -y @modelcontextprotocol/server-slack", "Slack messaging integration"),
            ],
            "search": [
                ("brave-search", "npx -y @modelcontextprotocol/server-brave-search", "Web search via Brave"),
            ],
        }

        query_lower = query.lower()
        matches = []
        for domain, servers in known_servers.items():
            if query_lower in domain:
                matches.extend(servers)

        if matches:
            table = Table(title=f"MCP Servers for '{query}'", border_style="cyan")
            table.add_column("Name", style="bold cyan")
            table.add_column("Command", style="dim")
            table.add_column("Description")

            for name, cmd, desc in matches:
                table.add_row(name, cmd, desc)

            console.print(table)
            console.print("\n[dim]To add a server: /mcp add <name>[/]")
            console.print("[dim]For more servers: https://github.com/modelcontextprotocol/servers[/]")
        else:
            console.print(f"[dim]No built-in suggestions for '{query}'.[/]")
            console.print("[dim]Browse available servers: https://github.com/modelcontextprotocol/servers[/]")
            console.print("[dim]Or search: https://mcp-registry.com[/]")
        return

    console.print(f"[warning]Unknown subcommand: /mcp {subcmd}[/]")
    console.print("[dim]Available: /mcp, /mcp connect, /mcp tools, /mcp add, /mcp remove, /mcp search, /mcp toggle[/]")


async def handle_skills_command(args: str):
    """Handle /skills command — list and manage skills."""
    registry = await _ensure_skill_registry()

    if registry is None:
        console.print("[error]Skills module not available.[/]")
        return

    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""

    if not subcmd:
        # List all skills
        skills = registry.list_skills()
        if not skills:
            console.print(Panel(
                "[dim]No skills installed.\n\n"
                "Skills are SKILL.md files in the [bold]skills/[/] directory.\n"
                "Each skill provides domain-specific knowledge to agents.[/]",
                title="[bold white]Skills[/]",
                border_style="cyan",
            ))
            return

        table = Table(
            title="Installed Skills",
            title_style="bold white",
            border_style="cyan",
            header_style="bold cyan",
        )
        table.add_column("Skill", style="bold")
        table.add_column("Mode", style="cyan")
        table.add_column("Description", style="white")
        table.add_column("Agents", style="dim")

        for skill in skills:
            table.add_row(
                skill["name"],
                skill["mode"],
                skill["description"][:50] or "-",
                ", ".join(skill.get("allowed_agents", []))[:30],
            )

        console.print()
        console.print(table)
        console.print("\n[dim]Skills provide domain knowledge to agents during conversations.[/]")
        return

    if subcmd == "reload":
        registry.reload()
        count = len(registry.list_skills())
        console.print(f"[success]\u2714 Reloaded {count} skill(s) from disk.[/]")
        return

    arg = parts[1].strip() if len(parts) > 1 else ""

    if subcmd == "pending":
        pending = registry.list_pending()
        if not pending:
            console.print("[dim]No pending skill candidates.[/]")
            return
        table = Table(
            title="Pending Skill Candidates",
            title_style="bold white",
            border_style="yellow",
            header_style="bold yellow",
        )
        table.add_column("Name", style="bold")
        table.add_column("Mode", style="cyan")
        table.add_column("Description", style="white")
        for skill in pending:
            table.add_row(skill.name, skill.mode, skill.description[:60] or "-")
        console.print()
        console.print(table)
        console.print("\n[dim]Use [bold]/skills review[/] to approve/reject, or [bold]/skills show <name>[/] to inspect.[/]")
        return

    if subcmd == "show":
        if not arg:
            console.print("[warning]Usage: /skills show <name>[/]")
            return
        skill = registry.skills.get(arg) or registry.get_pending(arg)
        if not skill:
            console.print(f"[error]No skill named '{arg}'.[/]")
            return
        console.print(Panel(
            f"[bold]{skill.name}[/] — [dim]{skill.mode}[/]\n"
            f"[dim]{skill.description}[/]\n"
            f"[dim]agents: {', '.join(skill.allowed_agents)}[/]\n\n"
            f"{skill.instructions}",
            title="[bold white]Skill[/]",
            border_style="cyan",
        ))
        return

    if subcmd == "approve":
        if not arg:
            console.print("[warning]Usage: /skills approve <name>[/]")
            return
        path = registry.approve_pending(arg)
        if path is None:
            console.print(f"[error]No pending candidate named '{arg}'.[/]")
            return
        console.print(f"[success]\u2714 Approved: {arg}[/]")
        return

    if subcmd == "reject":
        if not arg:
            console.print("[warning]Usage: /skills reject <name>[/]")
            return
        if registry.reject_pending(arg):
            console.print(f"[success]\u2714 Rejected: {arg}[/]")
        else:
            console.print(f"[error]No pending candidate named '{arg}'.[/]")
        return

    if subcmd == "delete":
        if not arg:
            console.print("[warning]Usage: /skills delete <name>[/]")
            return
        if registry.delete_approved(arg):
            console.print(f"[success]\u2714 Deleted approved skill: {arg}[/]")
        else:
            console.print(f"[error]No approved skill named '{arg}'.[/]")
        return

    if subcmd == "review":
        pending = registry.list_pending()
        if not pending:
            console.print("[dim]No pending skill candidates to review.[/]")
            return
        console.print(f"[info]{len(pending)} candidate(s) to review. Commands: [bold]a[/]=approve, [bold]r[/]=reject, [bold]s[/]=skip, [bold]q[/]=quit[/]")
        for idx, skill in enumerate(pending, 1):
            console.print()
            console.print(Panel(
                f"[bold]{skill.name}[/] — [dim]{skill.mode}[/]\n"
                f"[dim]{skill.description}[/]\n\n"
                f"{skill.instructions}",
                title=f"[bold yellow]Candidate {idx}/{len(pending)}[/]",
                border_style="yellow",
            ))
            try:
                answer = console.input("[bold]Action ([a]pprove / [r]eject / [s]kip / [q]uit): [/]").strip().lower()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Review cancelled.[/]")
                return
            if answer == "a" or answer == "approve":
                registry.approve_pending(skill.name)
                console.print(f"[success]\u2714 Approved: {skill.name}[/]")
            elif answer == "r" or answer == "reject":
                registry.reject_pending(skill.name)
                console.print(f"[warning]\u2717 Rejected: {skill.name}[/]")
            elif answer == "q" or answer == "quit":
                console.print("[dim]Review stopped.[/]")
                return
            else:
                console.print("[dim]Skipped.[/]")
        return

    console.print(f"[warning]Unknown: /skills {subcmd}.[/]")
    console.print("[dim]Available: /skills, /skills pending, /skills review, /skills show <name>, /skills approve <name>, /skills reject <name>, /skills delete <name>, /skills reload[/]")


async def handle_scan_command(args: str):
    """Scan a repository/directory to understand its structure."""
    import re as _re
    from agent_team.config import SENSITIVE_FILE_PATTERNS, SENSITIVE_EXTENSIONS, SENSITIVE_CONTENT_RE

    scan_path = args.strip() or state.user_cwd
    scan_path = os.path.expanduser(scan_path)

    if not Path(scan_path).exists():
        console.print(f"[error]Path does not exist: {scan_path}[/]")
        return

    console.print(f"[dim]Scanning: {scan_path}...[/]")

    def _is_sensitive_file(file_path: Path) -> bool:
        name_lower = file_path.name.lower()
        for pattern in SENSITIVE_FILE_PATTERNS:
            if pattern in name_lower:
                return True
        if file_path.suffix.lower() in SENSITIVE_EXTENSIONS:
            return True
        return False

    def _redact_sensitive(content: str) -> str:
        return _re.sub(SENSITIVE_CONTENT_RE, lambda m: m.group(1) + "=***REDACTED***", content)

    scan_result = []
    scan_path_obj = Path(scan_path)
    sensitive_skipped = 0

    # 1. Directory structure (max depth 3)
    scan_result.append("## Directory Structure")
    file_count = 0
    dir_count = 0
    extensions = {}

    for item in sorted(scan_path_obj.rglob("*")):
        # Skip hidden dirs, venv, node_modules, __pycache__, .git
        parts = item.relative_to(scan_path_obj).parts
        if any(p.startswith(".") or p in ("__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".git") for p in parts):
            continue

        rel = item.relative_to(scan_path_obj)
        depth = len(rel.parts)
        if depth > 3:
            continue

        # A4: Skip sensitive files
        if item.is_file() and _is_sensitive_file(item):
            sensitive_skipped += 1
            continue

        if item.is_file():
            file_count += 1
            ext = item.suffix.lower()
            extensions[ext] = extensions.get(ext, 0) + 1
            if depth <= 3:
                indent = "  " * (depth - 1)
                scan_result.append(f"{indent}\u251c\u2500\u2500 {item.name}")
        elif item.is_dir():
            dir_count += 1
            indent = "  " * (depth - 1)
            scan_result.append(f"{indent}\u251c\u2500\u2500 {item.name}/")

    scan_result.append(f"\nTotal: {file_count} files, {dir_count} directories")
    scan_result.append(f"Extensions: {', '.join(f'{k}({v})' for k, v in sorted(extensions.items(), key=lambda x: -x[1])[:10])}")

    # 2. Key files analysis (read important files)
    scan_result.append("\n## Key Files")
    key_patterns = ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.go", "*.rs", "*.java"]
    code_files = []
    for pattern in key_patterns:
        code_files.extend(scan_path_obj.rglob(pattern))

    # Filter out hidden/cache dirs and sensitive files
    code_files = [
        f for f in code_files
        if not any(p.startswith(".") or p in ("__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".git")
                   for p in f.relative_to(scan_path_obj).parts)
        and not _is_sensitive_file(f)
    ]

    # 3. Extract functions/classes from code files (up to 20 files)
    scan_result.append("\n## Functions & Classes")
    func_count = 0
    class_count = 0

    for code_file in sorted(code_files)[:20]:
        try:
            content = code_file.read_text(errors="ignore")
            rel_path = code_file.relative_to(scan_path_obj)

            # Python patterns
            funcs = _re.findall(r"^(?:async\s+)?def\s+(\w+)\s*\(", content, _re.MULTILINE)
            classes = _re.findall(r"^class\s+(\w+)", content, _re.MULTILINE)

            # JS/TS patterns
            if code_file.suffix in (".js", ".ts", ".jsx", ".tsx"):
                funcs += _re.findall(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", content)
                funcs += _re.findall(r"(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\(", content)
                classes += _re.findall(r"(?:export\s+)?class\s+(\w+)", content)

            if funcs or classes:
                scan_result.append(f"\n  {rel_path}:")
                if classes:
                    class_count += len(classes)
                    scan_result.append(f"    Classes: {', '.join(classes)}")
                if funcs:
                    func_count += len(funcs)
                    # Show max 10 functions per file
                    displayed = funcs[:10]
                    remaining = len(funcs) - 10
                    scan_result.append(f"    Functions: {', '.join(displayed)}" +
                                     (f" (+{remaining} more)" if remaining > 0 else ""))
        except Exception:
            continue

    scan_result.append(f"\nTotal: {func_count} functions, {class_count} classes across {len(code_files)} code files")

    # 3b. RAG — read content of key files (most important files by function count)
    scan_result.append("\n## Key File Contents (RAG)")
    # Score files by importance: more functions/classes = more important
    file_scores = {}
    for code_file in sorted(code_files)[:30]:
        try:
            content = code_file.read_text(errors="ignore")
            rel_path = str(code_file.relative_to(scan_path_obj))
            n_funcs = len(_re.findall(r"^(?:async\s+)?def\s+(\w+)\s*\(", content, _re.MULTILINE))
            n_classes = len(_re.findall(r"^class\s+(\w+)", content, _re.MULTILINE))
            # Boost files with "main", "app", "config", "scorer", "prompt" in name
            name_boost = 0
            for keyword in ("main", "app", "config", "scor", "prompt", "route", "model"):
                if keyword in code_file.name.lower():
                    name_boost += 3
            file_scores[code_file] = n_funcs + n_classes * 2 + name_boost
        except Exception:
            continue

    # Read top 5 most important files, cap at ~800 chars each
    top_files = sorted(file_scores.items(), key=lambda x: -x[1])[:5]
    rag_char_budget = 8000  # Total chars for RAG content
    chars_used = 0
    for code_file, score in top_files:
        if chars_used >= rag_char_budget:
            break
        try:
            content = code_file.read_text(errors="ignore")
            # A4: Redact sensitive values in file content
            content = _redact_sensitive(content)
            rel_path = str(code_file.relative_to(scan_path_obj))
            # Cap per file
            per_file_limit = min(2000, rag_char_budget - chars_used)
            if len(content) > per_file_limit:
                content = content[:per_file_limit] + "\n... [truncated]"
            scan_result.append(f"\n### FILE: {rel_path}")
            scan_result.append(f"(Use this exact path in your plan: {rel_path})")
            scan_result.append(f"```\n{content}\n```")
            chars_used += len(content)
        except Exception:
            continue

    if not top_files:
        scan_result.append("  No key files found to read.")

    # 4. Config files
    scan_result.append("\n## Configuration")
    config_files = ["pyproject.toml", "package.json", "Cargo.toml", "go.mod",
                    "requirements.txt", "Makefile", "Dockerfile", ".env.example",
                    "tsconfig.json", "setup.py", "setup.cfg"]
    found_configs = []
    for cf in config_files:
        if (scan_path_obj / cf).exists():
            found_configs.append(cf)
    if found_configs:
        scan_result.append(f"  Found: {', '.join(found_configs)}")
    else:
        scan_result.append("  No standard config files found")

    # 5. README if exists
    readme_path = scan_path_obj / "README.md"
    if not readme_path.exists():
        readme_path = scan_path_obj / "readme.md"
    if readme_path.exists():
        try:
            readme_content = readme_path.read_text(errors="ignore")
            # Just the first ~500 chars
            scan_result.append(f"\n## README Summary\n{readme_content[:500]}")
        except Exception:
            pass

    scan_text = "\n".join(scan_result)

    # Store in session
    state.session.add_scan_result(scan_text)

    # Display summary
    sensitive_msg = f"\n  [warning]Sensitive files skipped: {sensitive_skipped}[/]" if sensitive_skipped else ""
    console.print(Panel(
        f"[bold green]Scan complete![/]\n"
        f"  Files: {file_count}  |  Directories: {dir_count}\n"
        f"  Code files: {len(code_files)}  |  Functions: {func_count}  |  Classes: {class_count}\n"
        f"  Config: {', '.join(found_configs) if found_configs else 'none'}"
        f"{sensitive_msg}\n\n"
        f"[dim]Scan context stored in session. Agents will use this for better responses.[/]",
        title="[bold]\U0001f4c2 Repository Scan[/]",
        border_style="cyan",
    ))


async def _direct_reply(
    user_input: str,
    *,
    enable_web: bool,
    intent_label: str,
) -> None:
    """Direct LLM reply for CONVERSATION and QUERY intents.

    Injects the session's recent history so the agent can resolve "explain
    that" / "go on" references. When ``enable_web`` is True, a short hint is
    appended to the system prompt nudging the LLM to surface currency
    caveats (the runner-level web search isn't wired here — that's a TASK
    feature — so this is best-effort).
    """
    from agent_team.config import MODEL_ROUTING
    from agent_team.llm import call_llm, get_active_model, set_active_model

    system_prompt = (
        "You are a helpful AI assistant. Answer clearly and concisely. "
        "Use well-formatted code blocks when providing code examples."
    )
    if enable_web:
        system_prompt += (
            "\n\nThis is a factual question. If the answer depends on "
            "current/latest information (versions, releases, news, prices), "
            "say 'As of my training data …' and recommend the user verify "
            "with the official source."
        )

    messages: list[dict] = []
    for msg in state.session.messages[-8:]:
        if msg.role == "user":
            messages.append({"role": "user", "content": msg.content})
        elif msg.role == "agent":
            messages.append({"role": "assistant", "content": msg.content})
    messages.append({"role": "user", "content": user_input})

    # Route to fast model for lightweight replies when available.
    fast_model = MODEL_ROUTING.get("chat") or MODEL_ROUTING.get("ask")
    original_model = get_active_model()
    did_swap = False
    if fast_model and fast_model != original_model:
        try:
            set_active_model(fast_model)
            did_swap = True
        except Exception:
            did_swap = False

    header = {
        "conversation": "[bold]\U0001f4ac Chat[/]",
        "query": "[bold]\u2753 Query[/]",
    }.get(intent_label, "[bold]Reply[/]")

    console.print()
    console.print(f"[dim]Thinking…[/]", end="")
    try:
        response = await call_llm(
            system_prompt=system_prompt,
            messages=messages,
            temperature=0.5,
        )
        console.print("\r", end="")
        if response.strip():
            console.print()
            console.print(Panel(
                Markdown(response),
                title=header,
                border_style="dim cyan",
                padding=(1, 2),
            ))
            state.session.add_agent_output("assistant", response)
        else:
            console.print("\r[warning]No response from LLM. Check provider/model.[/]")
    except Exception as e:
        console.print(f"\r[error]Error: {e}[/]")
    finally:
        if did_swap:
            try:
                set_active_model(original_model)
            except Exception:
                pass


async def _confirm_execute() -> str:
    """Ask the user to approve, reject, or revise a plan. Returns one of
    ``"yes"``, ``"no"``, ``"revise:<text>"``."""
    try:
        answer = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: input(
                "\nProceed with execution? [Y = yes, n = no, r = revise]: "
            ).strip().lower(),
        )
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled.[/]")
        return "no"

    if answer in ("", "y", "yes"):
        return "yes"
    if answer in ("n", "no"):
        console.print("[dim]Plan discarded.[/]")
        return "no"
    if answer.startswith("r"):
        try:
            revision = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: input("What should change? > ").strip(),
            )
        except (EOFError, KeyboardInterrupt):
            return "no"
        if revision:
            return f"revise:{revision}"
        return "no"
    return "no"


async def _task_flow(user_input: str, detected_mode: str, plan_only_mode: bool) -> None:
    """Run the multi-agent pipeline for TASK intent with a confirm gate.

    ``plan_only_mode=True`` stops after planning regardless of the mode —
    used when the IntentRouter is uncertain. ``False`` lets coding/execution
    modes fall through to the existing execute-path prompt flow.
    """
    state.mcp_data_returned = False
    _, plan_outputs = await stream_conversation(user_input, detected_mode, plan_only=True)
    state.conversation_count += 1

    if state.mcp_data_returned:
        console.print("[dim]MCP tools returned data — no execution needed.[/]")
        return
    if plan_only_mode:
        return
    if detected_mode not in ("coding", "execution"):
        return

    verdict = await _confirm_execute()
    if verdict == "no":
        return
    if verdict.startswith("revise:"):
        revision = verdict.split(":", 1)[1]
        combined = f"{user_input}\n\n[Revision requested]: {revision}"
        console.print("[dim]Re-planning with your revision…[/]")
        state.session.add_user_message(f"[revision] {revision}")
        await _task_flow(combined, detected_mode, plan_only_mode=False)
        return

    # Yes → execute.
    detected_path = extract_path_from_text(user_input)
    if detected_path:
        console.print(f"[dim]Detected path: {detected_path} \u2014 executing here[/]")
        exec_path, should_exec = detected_path, True
    else:
        exec_path, should_exec = await ask_execute_location()
    if should_exec and exec_path:
        await stream_conversation(
            user_input, detected_mode,
            execution_path=exec_path, plan_only=False,
            reuse_plan=True, phase_outputs=plan_outputs,
        )
        state.conversation_count += 1


async def _dispatch_user_input(user_input: str) -> None:
    """Unified entry: classify intent, route to conversation / query / task.

    Replaces the old ``/ask`` / ``/chat`` / ``/plan`` / ``/exec`` slash
    commands plus the bare-input path. Every input goes through the same
    router and updates ``state.session`` so history is unified.
    """
    from agent_team.agents.intent import Intent, classify_intent

    state.session.add_user_message(user_input)
    intent = await classify_intent(user_input, session=state.session)

    # Minimal UX cue so users can see the routing decision.
    tag_color = {
        Intent.CONVERSATION: "#00d4aa",
        Intent.QUERY: "#7dc5ff",
        Intent.TASK: "#ffb36b",
    }.get(intent.intent, "#aaaaaa")
    console.print(
        f"[dim][italic][[/italic]"
        f"[{tag_color}]{intent.intent.value}[/{tag_color}]"
        f"[italic] {intent.reason} · {intent.source} "
        f"({intent.confidence:.2f})][/italic][/]"
    )

    if intent.intent == Intent.CONVERSATION:
        await _direct_reply(user_input, enable_web=False, intent_label="conversation")
        return

    if intent.intent == Intent.QUERY:
        await _direct_reply(user_input, enable_web=intent.needs_web, intent_label="query")
        return

    # TASK: pick a mode, check backend, run pipeline with confirm.
    if not state.backend_connected:
        connected = await check_backend()
        if not connected:
            console.print("[error]Backend not connected. Try /status to reconnect.[/]")
            return

    task_cls = intent.task_classification
    if task_cls and task_cls.mode_hint:
        detected_mode = task_cls.mode_hint
    else:
        detected_mode = auto_detect_mode(user_input)
    if detected_mode != state.mode:
        console.print(f"[dim]Mode: {MODE_ICONS.get(detected_mode, '')} {detected_mode}[/]")

    await check_tool_triggers(user_input)

    # Low-confidence TASK classifications stay in plan-only mode — we never
    # want to silently fire off an execution for an ambiguous input.
    plan_only_mode = intent.confidence < 0.7 or intent.source == "fallback"
    await _task_flow(user_input, detected_mode, plan_only_mode=plan_only_mode)

    followup = await handle_followup()
    while followup:
        state.session.add_user_message(followup)
        await _task_flow(followup, auto_detect_mode(followup), plan_only_mode=False)
        followup = await handle_followup()


async def check_tool_triggers(text: str) -> bool:
    """Check if user input triggers MCP/skills suggestions. Returns True if suggestions were shown."""
    registry = await _ensure_mcp_registry()
    skill_reg = await _ensure_skill_registry()
    if registry is None and skill_reg is None:
        return False

    try:
        from agent_team.mcp.triggers import suggest_tools_for_request
        skills_list = skill_reg.list_skills() if skill_reg else []
        result = suggest_tools_for_request(text, registry.config if registry else None, skills_list)

        suggestions = result.get("suggestions", [])
        if not suggestions:
            return False

        # Only show suggestions with reasonable confidence
        good_suggestions = [s for s in suggestions if s["confidence"] >= 0.3]
        if not good_suggestions:
            return False

        console.print()
        lines = []
        for s in good_suggestions[:3]:
            if s["type"] == "mcp":
                icon = "\U0001f50c"  # 🔌
                prefix = f"MCP server [bold cyan]{s['name']}[/]"
            elif s["type"] == "skill":
                icon = "\U0001f4da"  # 📚
                prefix = f"Skill [bold green]{s['name']}[/]"
            else:
                icon = "\U0001f4a1"  # 💡
                prefix = f"[bold yellow]{s['name']}[/]"
            lines.append(f"  {icon} {prefix} — {s['reason']}")

        console.print(Panel(
            "\n".join(lines),
            title="[bold]\U0001f50d Tool Suggestions[/]",
            border_style="yellow",
        ))

        # Only ask if MCP servers matched and are connected
        mcp_matches = [s for s in good_suggestions if s["type"] == "mcp"]
        if mcp_matches and registry:
            connected_tools = registry.get_all_tools()
            relevant = [t for t in connected_tools
                        if any(m["name"] == t.server_name for m in mcp_matches)]
            if relevant:
                choice = input("  Use these MCP tools? (y/n) [y]: ").strip().lower()
                if choice in ("", "y", "yes"):
                    return True  # Signal to inject tools into prompt

        return False

    except Exception:
        return False


def auto_detect_mode(text: str) -> str:
    """Auto-detect the best mode based on input keywords."""
    lower = text.lower()
    if any(kw in lower for kw in ["brainstorm", "ideas", "creative", "ideate"]):
        return "brainstorming"
    if any(kw in lower for kw in ["architect", "design system", "system design", "infrastructure"]):
        return "architecture"
    if any(kw in lower for kw in ["analyze", "reason", "logic", "prove", "think through", "why", "explain"]):
        return "thinking"
    if any(kw in lower for kw in ["run", "execute", "deploy", "start", "build and run"]):
        return "execution"
    return state.mode


def extract_path_from_text(text: str) -> str | None:
    """Extract an absolute or ~-prefixed directory path from user text."""
    import re as _re
    # Strategy: find absolute paths starting with / or ~/, then progressively
    # shorten from the right until we find a path that actually exists.
    match = _re.search(r'(?:^|\s)((?:/[^\n]+?))\s*(?:$|and\b|then\b|,|\))', text, _re.IGNORECASE)
    if not match:
        match = _re.search(r'(?:^|\s)(~(?:/[^\n]+?))\s*(?:$|and\b|then\b|,|\))', text, _re.IGNORECASE)
    if match:
        candidate = match.group(1).strip()
        expanded = os.path.expanduser(candidate)
        if Path(expanded).is_dir():
            return expanded
        # Try progressively removing trailing words (handles "path and do X")
        parts = candidate.rsplit(" ", 1)
        while len(parts) > 1:
            candidate = parts[0].strip()
            expanded = os.path.expanduser(candidate)
            if Path(expanded).is_dir():
                return expanded
            parts = candidate.rsplit(" ", 1)
    return None


# ── Plan Confirmation Flow ───────────────────────────────────────────────────

async def ask_execute_location() -> tuple[str | None, bool]:
    """After planning is complete, ask user if/where to execute.
    Returns: (execution_path, should_execute)"""
    console.print()
    console.print("[bold]Execute this plan?[/]")
    console.print("  [cyan]1)[/] No, just keep the plan")
    console.print("  [cyan]2)[/] Execute in current directory")
    console.print("  [cyan]3)[/] Execute in custom directory")
    console.print()

    choice = input("  Select [1/2/3]: ").strip()

    if choice == "2":
        cwd = state.user_cwd
        console.print(f"  [dim]Execution path: {cwd}[/]")
        return cwd, True
    elif choice == "3":
        path = input("  Enter directory path: ").strip()
        if path and Path(path).exists():
            return path, True
        elif path:
            console.print(f"[warning]Path does not exist: {path}[/]")
            return None, False
        return None, False
    else:
        return None, False


# ── Follow-up Handler ────────────────────────────────────────────────────────

async def handle_followup() -> str | None:
    """Ask if user has follow-up questions after a task completes."""
    console.print()
    console.print("[dim]\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500[/]")
    console.print("[bold]Any follow-up?[/] [dim](type your follow-up, or press Enter to skip)[/]")
    followup = input("  > ").strip()
    return followup if followup else None


# ── Feedback Commands ────────────────────────────────────────────────────────

async def handle_remember_command(args: str):
    """Handle /remember — store a user-specified rule."""
    text = args.strip()
    if not text:
        console.print("[warning]Usage: /remember <rule or preference>[/]")
        return

    from agent_team.memory.database import MemoryDB
    db = MemoryDB()
    try:
        feedback_id = await extract_and_store(
            user_msg=text,
            session_id=state.session.session_id,
            trigger="slash",
            rule=text,
            db=db,
        )
        if feedback_id:
            display = text[:60] + ("..." if len(text) > 60 else "")
            console.print(f"[success]\u2713 Remembered: {display}[/]")
        else:
            console.print("[error]Failed to store feedback.[/]")
    finally:
        db.close()


async def handle_forget_command(args: str):
    """Handle /forget — deactivate a feedback rule by ID or search query."""
    query = args.strip()
    if not query:
        console.print("[warning]Usage: /forget <id or search query>[/]")
        return

    import re as _re
    from agent_team.memory.database import MemoryDB
    db = MemoryDB()
    try:
        # Check if it looks like a hex UUID (32+ hex chars)
        if _re.match(r'^[0-9a-f]{32,}$', query):
            if db.deactivate_feedback(query):
                console.print(f"[success]\u2713 Deactivated feedback {query[:8]}...[/]")
            else:
                console.print(f"[warning]No active feedback found with ID {query[:8]}...[/]")
            return

        # Otherwise search by text
        matches = db.search_feedback(query)
        if not matches:
            console.print(f"[muted]No matching feedback found for '{query}'.[/]")
            return

        # Display numbered matches
        console.print()
        for i, m in enumerate(matches, 1):
            console.print(
                f"  [bold]{i}.[/] [{m['id'][:8]}] {m['rule'][:70]} "
                f"[dim](confidence: {m['confidence']:.2f})[/]"
            )
        console.print()
        console.print("[dim]Enter number to deactivate, or press Enter to cancel:[/]")

        choice = input("  > ").strip()
        if not choice:
            console.print("[muted]Cancelled.[/]")
            return

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                fid = matches[idx]["id"]
                if db.deactivate_feedback(fid):
                    console.print(f"[success]\u2713 Deactivated: {matches[idx]['rule'][:60]}[/]")
                else:
                    console.print("[error]Failed to deactivate.[/]")
            else:
                console.print("[warning]Invalid selection.[/]")
        except ValueError:
            console.print("[warning]Invalid selection.[/]")
    finally:
        db.close()


async def handle_learn_this_command():
    """Handle /learn-this — extract a rule from the last assistant message."""
    # Find the last agent message from session history
    last_agent_msg = None
    for msg in reversed(state.session.messages):
        if msg.role == "agent":
            last_agent_msg = msg.content
            break

    if not last_agent_msg:
        console.print("[warning]No recent assistant message to learn from.[/]")
        return

    from agent_team.memory.database import MemoryDB
    from agent_team.llm import call_llm

    db = MemoryDB()
    try:
        # Adapter to match the llm_provider.call() interface expected by detect_feedback
        class _LLMBridge:
            async def call(self, system_prompt, messages, temperature=0.1):
                return await call_llm(
                    system_prompt=system_prompt,
                    messages=messages,
                    temperature=temperature,
                )

        feedback_id = await extract_and_store(
            user_msg=last_agent_msg[:500],
            session_id=state.session.session_id,
            trigger="learn-this",
            db=db,
            llm_provider=_LLMBridge(),
        )
        if feedback_id:
            console.print(f"[success]\u2713 Learned from last assistant message (id: {feedback_id[:8]}...)[/]")
        else:
            console.print("[muted]No actionable feedback detected in the last message.[/]")
    except Exception as e:
        console.print(f"[error]Failed to extract feedback: {e}[/]")
    finally:
        db.close()


def handle_feedback_list_command():
    """Handle /feedback list — show all active feedback rules."""
    from agent_team.memory.database import MemoryDB
    db = MemoryDB()
    try:
        entries = db.list_active_feedback()
        if not entries:
            console.print("[muted]No active feedback rules. Use /remember to add one.[/]")
            return

        table = Table(
            title="Active Feedback Rules",
            title_style="bold white",
            border_style="cyan",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("ID", style="dim", max_width=8)
        table.add_column("Category", style="bold", max_width=10)
        table.add_column("Rule", style="white")
        table.add_column("Conf", justify="right", style="green", max_width=6)
        table.add_column("Used", justify="right", style="dim", max_width=5)

        for entry in entries:
            table.add_row(
                entry["id"][:8],
                entry.get("category") or "-",
                entry["rule"][:60],
                f"{entry['confidence']:.2f}",
                str(entry["times_applied"]),
            )

        console.print()
        console.print(table)
        console.print()
    finally:
        db.close()


# ── Bottom Toolbar ───────────────────────────────────────────────────────────

def get_bottom_toolbar():
    """Dynamic bottom toolbar for prompt_toolkit."""
    conn = "\u25cf Connected" if state.backend_connected else "\u25cf Disconnected"
    conn_color = "#00ff88" if state.backend_connected else "#ff4444"
    mode_icon = MODE_ICONS.get(state.mode, "")
    tokens = f" | Tokens: {state.total_session_tokens}" if state.total_session_tokens else ""

    # Session cost (C2) — only shown when non-zero to keep the bar short.
    cost_part = ""
    try:
        from agent_team.llm.pricing import current_session_usage
        total_cost = current_session_usage().total_cost()
        if total_cost > 0:
            cost_part = f" | ${total_cost:.4f}"
    except Exception:
        pass

    # Shorten the cwd for display
    cwd_display = state.user_cwd.replace(os.path.expanduser("~"), "~")

    return HTML(
        f'<style bg="#1a1a2e" fg="{conn_color}"> {conn} </style>'
        f'<style bg="#1a1a2e" fg="#aaaaaa"> | {cwd_display} | '
        f'{state.llm_provider}/{state.model} | '
        f'{mode_icon} {state.mode}{tokens}{cost_part} | /help </style>'
    )


# ── Main REPL Loop ───────────────────────────────────────────────────────────

async def main():
    """Main interactive CLI loop."""
    # Startup
    console.clear()

    # Auto-start backend if not running
    backend_was_started = False
    already_running = await _is_backend_running()
    if already_running:
        console.print(f"[dim]\u2714 Backend already running[/]")
    else:
        backend_was_started = await start_backend()

    # Check backend and populate state
    connected = await check_backend()
    if not connected:
        console.print(f"[error]\u2716 Cannot reach backend at {BACKEND_URL}[/]")
        console.print("[warning]Check that dependencies are installed: uv sync[/]")

    # Render the unified startup box (banner + status in one panel)
    console.clear()
    render_startup_box()
    console.print()
    console.print("[dim]Type [bold]/help[/bold] for available commands, or just start typing your request.[/]")
    console.print(Rule(style="dim"))

    # Setup prompt session with history
    session = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        style=pt_style,
    )

    while True:
        try:
            # Get user input with styled prompt and bottom toolbar
            user_input = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: session.prompt(
                    HTML('<style fg="#00d4aa" bold="true">\u276f </style>'),
                    bottom_toolbar=get_bottom_toolbar,
                ),
            )

            user_input = user_input.strip()
            if not user_input:
                continue

            # ── Slash command routing ────────────────────────────────────

            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                args = parts[1] if len(parts) > 1 else ""

                if cmd in ("/exit", "/quit", "/q"):
                    if backend_was_started:
                        console.print("[dim]Stopping backend...[/]")
                        stop_backend()
                    console.print("\n[dim]Goodbye! \U0001f44b[/]\n")
                    break

                elif cmd == "/help":
                    render_help()

                elif cmd == "/llm":
                    await handle_llm_command(args)

                elif cmd == "/key":
                    await handle_key_command(args)

                elif cmd == "/model":
                    await handle_model_command(args)

                elif cmd == "/mode":
                    handle_mode_command(args)

                elif cmd == "/mcp":
                    await handle_mcp_command(args)

                elif cmd == "/skills":
                    await handle_skills_command(args)

                elif cmd == "/scan":
                    await handle_scan_command(args)

                elif cmd == "/status":
                    await check_backend()
                    render_status_bar()

                elif cmd == "/tokens":
                    render_token_summary()

                elif cmd == "/cd":
                    new_dir = args.strip() if args else ""
                    if not new_dir:
                        console.print(f"[dim]Current: {state.user_cwd}[/]")
                        console.print("[warning]Usage: /cd <path>[/]")
                    else:
                        new_dir = os.path.expanduser(new_dir)
                        if not os.path.isabs(new_dir):
                            new_dir = os.path.join(state.user_cwd, new_dir)
                        new_dir = os.path.realpath(new_dir)
                        if os.path.isdir(new_dir):
                            state.user_cwd = new_dir
                            console.print(f"[success]Working directory: {new_dir}[/]")
                        else:
                            console.print(f"[error]Not a directory: {new_dir}[/]")

                elif cmd == "/pwd":
                    console.print(f"[accent]{state.user_cwd}[/]")

                elif cmd == "/clear":
                    console.clear()
                    render_banner()
                    render_status_bar()
                    console.print()

                elif cmd == "/history":
                    console.print(f"[dim]Conversations this session: {state.conversation_count}[/]")
                    console.print(f"[dim]Total tokens used: {state.total_session_tokens}[/]")

                elif cmd in ("/ask", "/chat", "/plan", "/exec"):
                    # These commands were removed — auto-routing now handles them.
                    # Redirect to the dispatcher so existing muscle memory still works.
                    console.print(
                        f"[dim]{cmd} was removed — the agent now auto-routes. "
                        "Just type your input directly next time.[/]"
                    )
                    if args.strip():
                        await _dispatch_user_input(args.strip())

                elif cmd == "/remember":
                    await handle_remember_command(args)

                elif cmd == "/forget":
                    await handle_forget_command(args)

                elif cmd == "/learn-this":
                    await handle_learn_this_command()

                elif cmd == "/feedback":
                    if args.strip().lower() == "list":
                        handle_feedback_list_command()
                    else:
                        console.print("[warning]Usage: /feedback list[/]")

                else:
                    console.print(f"[warning]Unknown command: {cmd}. Type /help for available commands.[/]")

                continue

            # ── Regular message ──────────────────────────────────────────

            if not state.backend_connected:
                connected = await check_backend()
                if not connected:
                    console.print("[error]Backend not connected. Try /status to reconnect.[/]")
                    continue

            # ── Auto-detect user feedback (non-blocking) ──────────────
            try:
                if (
                    len(user_input) >= 15
                    and state.auto_feedback_count < MAX_AUTO_PER_SESSION
                ):
                    from agent_team.llm import call_llm as _call_llm

                    class _AutoBridge:
                        async def call(self, system_prompt, messages, temperature=0.1):
                            return await _call_llm(
                                system_prompt=system_prompt,
                                messages=messages,
                                temperature=temperature,
                            )

                    _fb_result = await detect_feedback(user_input, _AutoBridge())
                    if _fb_result:
                        from agent_team.memory.database import MemoryDB as _MemDB
                        _fb_db = _MemDB()
                        try:
                            _fb_id = await extract_and_store(
                                user_msg=user_input,
                                session_id=state.session.session_id,
                                trigger="auto",
                                db=_fb_db,
                                llm_provider=_AutoBridge(),
                            )
                            if _fb_id:
                                _rule = _fb_result.get("rule", "")[:60]
                                console.print(f"[dim][learned: {_rule}][/]")
                                state.auto_feedback_count += 1
                        finally:
                            _fb_db.close()
            except Exception:
                pass  # Never break the main loop

            # Auto-route via IntentRouter (CONVERSATION / QUERY / TASK).
            await _dispatch_user_input(user_input)

        except KeyboardInterrupt:
            console.print("\n[dim]Use /exit to quit[/]")
            continue
        except EOFError:
            if backend_was_started:
                console.print("[dim]Stopping backend...[/]")
                stop_backend()
            console.print("\n[dim]Goodbye! \U0001f44b[/]\n")
            break
        except Exception as e:
            console.print(f"[error]Error: {e}[/]")
            continue


def main_sync():
    """Sync entry point for pyproject.toml console_scripts."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[dim]Goodbye![/]\n")
    finally:
        stop_backend()


if __name__ == "__main__":
    main_sync()
