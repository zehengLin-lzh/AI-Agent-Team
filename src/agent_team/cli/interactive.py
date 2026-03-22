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
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedError

# ── Branding & Config ────────────────────────────────────────────────────────

APP_NAME = "Agent Team"
APP_VERSION = "2.3.0"
BACKEND_URL = os.getenv("AGENT_TEAM_BACKEND_URL", "http://localhost:8000")
HISTORY_FILE = Path.home() / ".agent_team_history"

AGENT_ICONS = {
    "ORCHESTRATOR": "\u2692",   # ⚒
    "THINKER": "\U0001f9e0",    # 🧠
    "PLANNER": "\U0001f4d0",    # 📐
    "EXECUTOR": "\u26a1",       # ⚡
    "REVIEWER": "\U0001f50d",   # 🔍
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
    "agent.orchestrator": "bold green",
    "agent.thinker": "bold magenta",
    "agent.planner": "bold yellow",
    "agent.executor": "bold cyan",
    "agent.reviewer": "bold red",
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

state = CLIState()


# ── Helpers ──────────────────────────────────────────────────────────────────

def ws_url() -> str:
    return BACKEND_URL.replace("https://", "wss://").replace("http://", "ws://") + "/ws"


async def check_backend() -> bool:
    """Check backend health and populate state."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{BACKEND_URL}/health")
            data = r.json()
            state.backend_connected = data.get("status") == "ok"
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
    """Show startup banner."""
    banner_text = Text()
    banner_text.append("  _____                    _     _____                    \n", style="bold cyan")
    banner_text.append(" |  _  |                  | |   |_   _|                   \n", style="bold cyan")
    banner_text.append(" | |_| | __ _  ___ _ __  | |_    | | ___  __ _ _ __ ___  \n", style="bold cyan")
    banner_text.append(" |  _  |/ _` |/ _ \\ '_ \\ | __|   | |/ _ \\/ _` | '_ ` _ \\ \n", style="bold blue")
    banner_text.append(" | | | | (_| |  __/ | | || |_    | |  __/ (_| | | | | | |\n", style="bold blue")
    banner_text.append(" |_| |_|\\__, |\\___|_| |_| \\__|   |_|\\___|\\__,_|_| |_| |_|\n", style="bold magenta")
    banner_text.append("         __/ |                                             \n", style="bold magenta")
    banner_text.append("        |___/                                              \n", style="bold magenta")

    console.print(Panel(
        banner_text,
        title=f"[bold white]{APP_NAME} v{APP_VERSION}[/]",
        subtitle="[dim]Self-learning local AI agent team[/]",
        border_style="cyan",
        padding=(0, 2),
    ))


def render_status_bar():
    """Render current status info."""
    status = Table.grid(padding=(0, 2))
    status.add_column(justify="left")
    status.add_column(justify="left")
    status.add_column(justify="left")
    status.add_column(justify="right")

    conn_icon = "[green]\u25cf[/]" if state.backend_connected else "[red]\u25cf[/]"
    conn_text = "Connected" if state.backend_connected else "Disconnected"

    mode_icon = MODE_ICONS.get(state.mode, "\U0001f4bb")

    status.add_row(
        f"{conn_icon} {conn_text}",
        f"[accent]LLM:[/] {state.llm_provider}",
        f"[accent]Model:[/] {state.model}",
        f"[accent]Mode:[/] {mode_icon} {state.mode}",
    )
    console.print(Panel(status, border_style="dim blue", padding=(0, 1)))


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
        ("/llm [provider]", "Switch LLM provider (e.g., /llm huggingface) or list providers"),
        ("/model [name]", "Switch the active model (e.g., /model llama3.2)"),
        ("/model list", "List all available models"),
        ("/mode <mode>", "Switch mode: thinking, coding, brainstorming, architecture, execution"),
        ("/status", "Show current connection status and model info"),
        ("/tokens", "Show token usage for the current session"),
        ("/clear", "Clear the screen"),
        ("/history", "Show conversation history summary"),
        ("/plan <task>", "Submit a task in plan-only mode (no execution)"),
        ("/exec <task>", "Submit a task with execution enabled"),
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


def render_agent_header(agent_name: str, model: str = ""):
    """Show a styled agent header."""
    icon = AGENT_ICONS.get(agent_name, "\u2022")
    style_name = f"agent.{agent_name.lower()}"
    model_tag = f" [dim]({model})[/]" if model else ""
    console.print()
    console.print(Rule(
        f"[{style_name}]{icon} {agent_name}[/]{model_tag}",
        style=style_name.replace("agent.", ""),
    ))


def render_phase_header(phase: str, label: str):
    """Show phase transition."""
    phase_icons = {
        "intake": "\U0001f4e5",   # 📥
        "think": "\U0001f914",    # 🤔
        "plan": "\U0001f4cb",     # 📋
        "execute": "\u2699\ufe0f",  # ⚙️
        "verify": "\u2705",       # ✅
    }
    icon = phase_icons.get(phase, "\u25b6")
    console.print()
    console.print(Panel(
        f"[bold]{icon} {label}[/]",
        border_style="blue",
        padding=(0, 2),
    ))


# ── Streaming Handler ────────────────────────────────────────────────────────

async def stream_conversation(
    plan_text: str,
    agent_mode: str,
    execution_path: str | None = None,
    plan_only: bool = False,
) -> dict | None:
    """Connect via WebSocket and stream the agent team output with rich formatting."""

    ws_mode = "plan_only" if plan_only else ("plan_and_execute" if execution_path else "plan_only")

    if execution_path:
        full_plan = f"{plan_text}\n\nExecution context:\n- Requested path: {execution_path}\n- Mode: {agent_mode}"
    else:
        full_plan = f"{plan_text}\n\nExecution context:\n- No execution path selected\n- Mode: {agent_mode}"

    token_summary = None

    try:
        async with ws_connect(ws_url(), max_size=10 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "type": "start",
                "content": full_plan,
                "mode": agent_mode if not plan_only else ws_mode,
                "execution_path": execution_path,
            }))

            current_buffer = ""
            current_agent = None

            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type")

                if t == "status":
                    phase = msg.get("phase", "")
                    label = msg.get("message", phase)
                    render_phase_header(phase, label)

                elif t == "agent_start":
                    current_agent = msg.get("agent", "")
                    model = msg.get("model", state.model)
                    current_buffer = ""
                    render_agent_header(current_agent, model)

                elif t == "token":
                    token = msg.get("content", "")
                    sys.stdout.write(token)
                    sys.stdout.flush()
                    current_buffer += token

                elif t == "agent_done":
                    if current_buffer:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                    # Show per-agent token stats
                    ts = msg.get("token_stats")
                    if ts and ts.get("total_tokens", 0) > 0:
                        console.print(
                            f"[token]  \u2514\u2500 tokens: {ts['prompt_tokens']} prompt + "
                            f"{ts['completion_tokens']} completion = {ts['total_tokens']} total "
                            f"({ts.get('tokens_per_second', 0):.1f} t/s)[/]"
                        )
                    current_agent = None

                elif t == "memory_context":
                    results = msg.get("results", [])
                    if results:
                        console.print()
                        console.print(Panel(
                            "\n".join(
                                f"[dim]\u2022 [{r.get('source', '?')}] {r.get('content', '')[:120]}...[/]"
                                for r in results[:3]
                            ),
                            title="[bold blue]\U0001f4da Memory Context[/]",
                            border_style="blue",
                        ))

                elif t == "learning_complete":
                    patterns = msg.get("patterns_extracted", 0)
                    if patterns:
                        console.print(f"\n[success]\U0001f4a1 Learned {patterns} new pattern(s) from this session.[/]")

                elif t == "waiting_for_user":
                    question = msg.get("question", "")
                    console.print()
                    console.print(Panel(
                        f"[bold yellow]{question}[/]",
                        title="[bold]\u2753 Agent needs your input[/]",
                        border_style="yellow",
                    ))
                    # Use simple input here since we're in async context
                    user_reply = input("\n  Your answer: ").strip()
                    await ws.send(json.dumps({"content": user_reply}))

                elif t == "complete":
                    token_summary = msg.get("token_summary")
                    model_used = msg.get("model", state.model)
                    console.print()
                    console.print(Panel(
                        f"[bold green]\u2714 Task complete[/]  |  Model: [accent]{model_used}[/]",
                        border_style="green",
                    ))
                    if token_summary:
                        state.session_tokens = token_summary
                        state.total_session_tokens += token_summary.get("total", 0)
                        render_token_summary(token_summary)
                    break

                elif t == "error":
                    console.print(f"\n[error]\u2716 Error: {msg.get('content', 'Unknown error')}[/]")
                    break

    except ConnectionClosedError as e:
        console.print(f"\n[error]Connection closed unexpectedly: {e}[/]")
    except ConnectionRefusedError:
        console.print("[error]Cannot connect to backend. Is it running? Try: ./start.sh[/]")

    return token_summary


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
        console.print("[dim]  Providers: ollama (local), huggingface (API or local TGI)[/]")
        console.print("[dim]  For HuggingFace: export HF_TOKEN='hf_...' before starting backend[/]")
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


# ── Plan Confirmation Flow ───────────────────────────────────────────────────

async def confirm_execution(plan_text: str, detected_mode: str) -> tuple[bool, str | None, bool]:
    """
    Ask user to confirm before execution.
    Returns: (should_proceed, execution_path, plan_only)
    """
    console.print()
    console.print(Panel(
        f"[bold]Mode:[/] {MODE_ICONS.get(detected_mode, '')} {detected_mode}\n"
        f"[bold]Task:[/] {plan_text[:200]}{'...' if len(plan_text) > 200 else ''}",
        title="[bold cyan]\U0001f680 Ready to process[/]",
        border_style="cyan",
    ))

    if detected_mode in ("coding", "execution"):
        console.print("\n[bold]How should this be handled?[/]")
        console.print("  [cyan]1)[/] Plan only (analyze and plan, no file changes)")
        console.print("  [cyan]2)[/] Plan + Execute in current directory")
        console.print("  [cyan]3)[/] Plan + Execute in custom directory")
        console.print("  [cyan]c)[/] Cancel")
        console.print()

        choice = input("  Select [1/2/3/c]: ").strip().lower()

        if choice == "c":
            console.print("[muted]Cancelled.[/]")
            return False, None, False
        elif choice == "2":
            cwd = os.getcwd()
            console.print(f"  [dim]Execution path: {cwd}[/]")
            return True, cwd, False
        elif choice == "3":
            path = input("  Enter directory path: ").strip()
            if path and Path(path).exists():
                return True, path, False
            elif path:
                console.print(f"[warning]Path does not exist: {path}[/]")
                return False, None, False
            else:
                return True, None, True
        else:
            return True, None, True
    else:
        console.print("\n[bold]Proceed?[/] [cyan](y/n)[/] ", end="")
        confirm = input().strip().lower()
        if confirm in ("n", "no", "c", "cancel"):
            console.print("[muted]Cancelled.[/]")
            return False, None, False
        return True, None, True


# ── Follow-up Handler ────────────────────────────────────────────────────────

async def handle_followup() -> str | None:
    """Ask if user has follow-up questions after a task completes."""
    console.print()
    console.print("[dim]\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500[/]")
    console.print("[bold]Any follow-up?[/] [dim](type your follow-up, or press Enter to skip)[/]")
    followup = input("  > ").strip()
    return followup if followup else None


# ── Bottom Toolbar ───────────────────────────────────────────────────────────

def get_bottom_toolbar():
    """Dynamic bottom toolbar for prompt_toolkit."""
    conn = "\u25cf Connected" if state.backend_connected else "\u25cf Disconnected"
    conn_color = "#00ff88" if state.backend_connected else "#ff4444"
    mode_icon = MODE_ICONS.get(state.mode, "")
    tokens = f" | Tokens: {state.total_session_tokens}" if state.total_session_tokens else ""

    return HTML(
        f'<style bg="#1a1a2e" fg="{conn_color}"> {conn} </style>'
        f'<style bg="#1a1a2e" fg="#aaaaaa"> | LLM: {state.llm_provider} | Model: {state.model} | '
        f'Mode: {mode_icon} {state.mode}{tokens} | /help for commands </style>'
    )


# ── Main REPL Loop ───────────────────────────────────────────────────────────

async def main():
    """Main interactive CLI loop."""
    # Startup
    console.clear()
    render_banner()

    # Check backend
    console.print("[dim]Connecting to backend...[/]", end=" ")
    connected = await check_backend()
    if connected:
        console.print(f"[success]\u2714 Connected[/] [dim]({BACKEND_URL})[/]")
        console.print(f"[dim]  Model: {state.model} | Models available: {len(state.available_models)}[/]")
    else:
        console.print(f"[error]\u2716 Cannot reach backend at {BACKEND_URL}[/]")
        console.print("[warning]Start the backend first: ./start.sh[/]")
        console.print("[dim]Some features may be unavailable.[/]")

    console.print()
    render_status_bar()
    console.print()
    console.print("[dim]Type [bold]/help[/bold] for available commands, or just start typing your request.[/]")
    console.print()

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
                    console.print("\n[dim]Goodbye! \U0001f44b[/]\n")
                    break

                elif cmd == "/help":
                    render_help()

                elif cmd == "/llm":
                    await handle_llm_command(args)

                elif cmd == "/model":
                    await handle_model_command(args)

                elif cmd == "/mode":
                    handle_mode_command(args)

                elif cmd == "/status":
                    await check_backend()
                    render_status_bar()

                elif cmd == "/tokens":
                    render_token_summary()

                elif cmd == "/clear":
                    console.clear()
                    render_banner()
                    render_status_bar()
                    console.print()

                elif cmd == "/history":
                    console.print(f"[dim]Conversations this session: {state.conversation_count}[/]")
                    console.print(f"[dim]Total tokens used: {state.total_session_tokens}[/]")

                elif cmd == "/plan":
                    if not args:
                        console.print("[warning]Usage: /plan <your task description>[/]")
                        continue
                    if not state.backend_connected:
                        console.print("[error]Backend not connected. Run ./start.sh first.[/]")
                        continue
                    detected = auto_detect_mode(args)
                    console.print(f"[dim]Auto-detected mode: {MODE_ICONS.get(detected, '')} {detected}[/]")
                    await stream_conversation(args, detected, plan_only=True)
                    state.conversation_count += 1
                    followup = await handle_followup()
                    if followup:
                        detected2 = auto_detect_mode(followup)
                        await stream_conversation(followup, detected2, plan_only=True)
                        state.conversation_count += 1

                elif cmd == "/exec":
                    if not args:
                        console.print("[warning]Usage: /exec <your task description>[/]")
                        continue
                    if not state.backend_connected:
                        console.print("[error]Backend not connected. Run ./start.sh first.[/]")
                        continue
                    detected = auto_detect_mode(args)
                    if detected not in ("coding", "execution"):
                        detected = "coding"
                    proceed, exec_path, plan_only = await confirm_execution(args, detected)
                    if proceed:
                        await stream_conversation(args, detected, execution_path=exec_path, plan_only=plan_only)
                        state.conversation_count += 1

                else:
                    console.print(f"[warning]Unknown command: {cmd}. Type /help for available commands.[/]")

                continue

            # ── Regular message ──────────────────────────────────────────

            if not state.backend_connected:
                connected = await check_backend()
                if not connected:
                    console.print("[error]Backend not connected. Run ./start.sh first.[/]")
                    continue

            # Auto-detect mode
            detected_mode = auto_detect_mode(user_input)
            if detected_mode != state.mode:
                console.print(f"[dim]Auto-detected mode: {MODE_ICONS.get(detected_mode, '')} {detected_mode}[/]")

            # Confirm before proceeding
            proceed, exec_path, plan_only = await confirm_execution(user_input, detected_mode)
            if not proceed:
                continue

            await stream_conversation(
                user_input, detected_mode,
                execution_path=exec_path, plan_only=plan_only,
            )
            state.conversation_count += 1

            # Follow-up
            followup = await handle_followup()
            while followup:
                detected2 = auto_detect_mode(followup)
                await stream_conversation(followup, detected2, plan_only=True)
                state.conversation_count += 1
                followup = await handle_followup()

        except KeyboardInterrupt:
            console.print("\n[dim]Use /exit to quit[/]")
            continue
        except EOFError:
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


if __name__ == "__main__":
    main_sync()
