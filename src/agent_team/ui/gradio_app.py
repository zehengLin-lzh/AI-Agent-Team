"""
Gradio-based frontend for the local Agent Team v2.

Provides a lightweight UI on top of the FastAPI backend /ask endpoint
with mode selection, memory display, and learning stats.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
import gradio as gr


BACKEND_URL = os.getenv("AGENT_TEAM_BACKEND_URL", "http://localhost:8000")

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = ROOT_DIR / "agent_team.config.json"
THEME_ENV = "AGENT_TEAM_THEME"

AGENT_MODES = ["coding", "thinking", "brainstorming", "architecture", "execution"]


def _get_theme_name() -> str:
    env = os.getenv(THEME_ENV)
    if env:
        return env.strip().lower()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            theme_val = str(data.get("theme", "")).strip().lower()
            if theme_val in {"light", "dark"}:
                return theme_val
        except Exception:
            pass
    return "light"


async def submit_plan(
    plan: str,
    agent_mode: str,
    execution_location: str,
    custom_dir: str,
    mode_label: str,
) -> Tuple[str, str, str, str, str]:
    """Call the backend /ask endpoint and return per-phase markdown."""
    plan = (plan or "").strip()
    if not plan:
        return ("Please enter a request.", "", "", "", "")

    if execution_location == "current directory":
        execution_path: Optional[str] = os.getcwd()
    else:
        execution_path = (custom_dir or "").strip() or None

    if mode_label == "Plan only":
        mode = "plan_only"
    else:
        mode = "plan_and_execute"

    if not execution_path and mode == "plan_and_execute":
        mode = "plan_only"

    payload: Dict[str, Any] = {
        "plan": plan,
        "mode": mode,
        "agent_mode": agent_mode,
        "execution_path": execution_path,
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(f"{BACKEND_URL}/ask", json=payload)
        r.raise_for_status()
        data = r.json()

    info_lines = [
        f"**Title**: {data.get('title')}",
        f"**Mode**: {data.get('mode')}",
        f"**Agent Mode**: {data.get('agent_mode', 'coding')}",
        f"**Execution path**: {data.get('execution_path') or '(none)'}",
        f"**Plan file**: `{data.get('plan_file_path')}`",
    ]
    meta_md = "\n".join(info_lines)

    phases: Dict[str, str] = data.get("phase_outputs", {}) or {}

    intake_md = phases.get("ORCHESTRATOR", "")
    think_md = phases.get("THINKER", "")
    plan_md = phases.get("PLANNER", "")

    executor_md = phases.get("EXECUTOR", "")
    reviewer_md = phases.get("REVIEWER", "")
    output_md = executor_md
    if reviewer_md:
        output_md += "\n\n---\n\n" + reviewer_md

    return meta_md, intake_md, think_md, plan_md, output_md


async def check_health() -> str:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{BACKEND_URL}/health")
            data = r.json()
        if data.get("status") == "ok" and data.get("model_available"):
            return f"Backend: online — Model: `{data.get('model')}`"
        if data.get("status") == "ok":
            return f"Backend: online — Model `{data.get('model')}` not pulled"
        return f"Backend: error {data}"
    except Exception as exc:
        return f"Backend: offline ({exc})"


async def get_stats() -> str:
    try:
        from agent_team.learning.patterns import get_learning_stats
        s = get_learning_stats()
        return (
            f"Sessions: {s['total_sessions']} | "
            f"Memory chunks: {s['total_chunks']} | "
            f"Patterns: {s['total_patterns']}"
        )
    except Exception:
        return "Learning stats unavailable"


LIGHT_THEME = gr.themes.Soft()
DARK_THEME = gr.themes.Monochrome()
_theme_name = _get_theme_name()
CURRENT_THEME = DARK_THEME if _theme_name == "dark" else LIGHT_THEME


with gr.Blocks(title="Agent Team v2", theme=CURRENT_THEME) as demo:
    gr.Markdown("## Agent Team v2 — Self-Learning Local AI Agent Platform")

    with gr.Row():
        health_md = gr.Markdown("Checking backend health...")
        stats_md = gr.Markdown("")

    with gr.Row():
        plan_box = gr.Textbox(
            lines=8,
            label="Your request",
            placeholder=(
                "Describe what you need...\n\n"
                "Examples:\n"
                "  Coding: Build a user auth system with FastAPI backend\n"
                "  Thinking: Analyze the pros/cons of microservices vs monolith\n"
                "  Brainstorming: Ideas for improving developer productivity\n"
                "  Architecture: Design a real-time notification system"
            ),
        )
        with gr.Column():
            agent_mode_sel = gr.Dropdown(
                choices=AGENT_MODES,
                value="coding",
                label="Agent Mode",
            )
            execution_location = gr.Radio(
                choices=["current directory", "custom directory"],
                value="current directory",
                label="Execution location",
            )
            custom_dir = gr.Textbox(
                label="Custom directory",
                placeholder="/path/to/project (optional)",
                visible=False,
            )
            mode_sel = gr.Radio(
                choices=["Plan only", "Plan and execute"],
                value="Plan only",
                label="Execution mode",
            )
            run_btn = gr.Button("Run Agent Team", variant="primary")

    with gr.Accordion("Run details", open=True):
        meta_out = gr.Markdown("")

    with gr.Accordion("01 · Orchestrator", open=False):
        intake_out = gr.Markdown("")

    with gr.Accordion("02 · Thinker", open=False):
        think_out = gr.Markdown("")

    with gr.Accordion("03 · Planner", open=True):
        plan_out = gr.Markdown("")

    with gr.Accordion("04 · Output + Review", open=False):
        execute_out = gr.Markdown("")

    def on_location_change(choice: str):
        return gr.update(visible=(choice == "custom directory"))

    execution_location.change(
        on_location_change, inputs=execution_location, outputs=custom_dir
    )

    run_btn.click(
        submit_plan,
        inputs=[plan_box, agent_mode_sel, execution_location, custom_dir, mode_sel],
        outputs=[meta_out, intake_out, think_out, plan_out, execute_out],
    )

    demo.load(check_health, inputs=None, outputs=health_md)
    demo.load(get_stats, inputs=None, outputs=stats_md)


if __name__ == "__main__":
    demo.launch()
