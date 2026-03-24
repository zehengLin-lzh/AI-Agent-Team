"""Agent Team Backend -- FastAPI application."""
import json
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from agent_team.config import REPO_ROOT
from agent_team.agents.runner import AgentTeam
from agent_team.llm import get_active_model, set_active_model
from agent_team.llm.registry import (
    get_provider, set_provider, get_active_provider_name, list_providers,
)
from agent_team.agents.http_runner import (
    AskMode, AskRequest, AskResponse, run_team_http,
)
from agent_team.agents.definitions import AgentMode
from agent_team.plans.storage import save_plan_markdown

app = FastAPI(title="Mat Agent Team v2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# NOTE: Launch uvicorn with generous WebSocket keepalive to support user-input waits:
#   uvicorn ... --ws-ping-interval 30 --ws-ping-timeout 300
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        data = json.loads(raw)
        if data.get("type") == "start":
            team = AgentTeam(
                websocket,
                execution_path=data.get("execution_path"),
                plan_only=data.get("plan_only", False),
                reuse_plan=data.get("reuse_plan", False),
                prior_phase_outputs=data.get("phase_outputs"),
            )

            # Inject session context if provided
            session_ctx = data.get("session_context", "")
            if session_ctx:
                team.session_context = session_ctx

            # Inject MCP tools if available
            try:
                from agent_team.mcp.registry import MCPRegistry
                mcp_reg = MCPRegistry()
                await mcp_reg.connect_all()
                tools_prompt = mcp_reg.format_tools_prompt()
                if tools_prompt:
                    team.mcp_tools_prompt = tools_prompt
                    team.mcp_registry = mcp_reg
            except Exception:
                pass

            mode = data.get("mode", "coding")
            await team.run(data["content"], mode=mode)
    except WebSocketDisconnect:
        pass


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    import traceback as _tb
    user_plan = request.plan.strip()
    if not user_plan:
        raise ValueError("plan must not be empty")

    mode = request.mode or AskMode.PLAN_ONLY
    agent_mode_str = request.agent_mode or "coding"
    try:
        agent_mode = AgentMode(agent_mode_str)
    except ValueError:
        agent_mode = AgentMode.CODING

    execution_path = (request.execution_path or "").strip() or None
    if not execution_path and mode == AskMode.PLAN_AND_EXECUTE:
        mode = AskMode.PLAN_ONLY

    if execution_path:
        user_plan_with_context = (
            f"{user_plan}\n\nExecution context:\n- Path: {execution_path}\n- Mode: {mode.value}"
        )
    else:
        user_plan_with_context = f"{user_plan}\n\nExecution context:\n- Mode: {mode.value}"

    try:
        phase_outputs = await run_team_http(
            user_plan_with_context, mode,
            agent_mode=agent_mode, execution_path=execution_path,
        )
    except Exception as e:
        print(f"[/ask] Pipeline error: {e}")
        _tb.print_exc()
        phase_outputs = {"ERROR": str(e)}

    planner_output = phase_outputs.get("PLANNER", "") or user_plan
    first_line = next((ln.strip() for ln in planner_output.splitlines() if ln.strip()), "")
    title = first_line[:100] if first_line else "Agent Team Plan"

    plan_path = save_plan_markdown(
        title=title, plan_text=planner_output,
        execution_path=execution_path, mode=agent_mode.value,
    )

    # Extract file_changes from phase_outputs (stored by run_team_http as list[dict])
    file_changes = phase_outputs.pop("_file_changes", None)  # type: ignore[arg-type]

    return AskResponse(
        title=title,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        mode=mode,
        agent_mode=agent_mode.value,
        execution_path=execution_path,
        plan_file_path=str(plan_path),
        phase_outputs=phase_outputs,
        file_changes=file_changes,
    )


@app.get("/health")
async def health():
    provider = get_provider()
    result = await provider.health_check()
    result["active_provider"] = get_active_provider_name()
    return result


@app.get("/models")
async def list_models():
    provider = get_provider()
    models = await provider.list_models()
    return {
        "provider": get_active_provider_name(),
        "active_model": get_active_model(),
        "models": [{"name": m} for m in models],
    }


@app.post("/models/switch")
async def switch_model(body: dict):
    model_name = body.get("model", "").strip()
    if not model_name:
        return {"error": "model name required"}
    provider = get_provider()
    available = await provider.list_models()
    if available and not any(model_name in m for m in available):
        return {"error": f"Model '{model_name}' not found", "available": available}
    set_active_model(model_name)
    return {
        "status": "ok",
        "provider": get_active_provider_name(),
        "active_model": model_name,
    }


@app.get("/providers")
async def get_providers():
    """List available LLM providers and their status."""
    results = []
    for name in list_providers():
        p = get_provider(name)
        health = await p.health_check()
        results.append({
            "name": name,
            "active": name == get_active_provider_name(),
            "model": p.get_active_model(),
            "status": health.get("status", "unknown"),
        })
    return {"providers": results, "active": get_active_provider_name()}


@app.post("/providers/switch")
async def switch_provider(body: dict):
    """Switch the active LLM provider."""
    provider_name = body.get("provider", "").strip()
    if not provider_name:
        return {"error": "provider name required"}
    try:
        set_provider(provider_name)
        return {
            "status": "ok",
            "provider": provider_name,
            "model": get_active_model(),
        }
    except ValueError as e:
        return {"error": str(e)}


@app.get("/mcp/status")
async def mcp_status():
    """Get MCP server status and available tools."""
    try:
        from agent_team.mcp.registry import MCPRegistry
        registry = MCPRegistry()
        statuses = registry.get_statuses()
        tools = registry.get_all_tools()
        return {
            "servers": [
                {
                    "name": s.name, "type": s.type,
                    "connected": s.connected, "enabled": s.enabled,
                    "tools": len(s.tools), "description": s.description,
                }
                for s in statuses
            ],
            "total_tools": len(tools),
        }
    except Exception:
        return {"servers": [], "total_tools": 0}


# Serve frontend static files
_pkg_dir = Path(__file__).resolve().parent.parent
frontend_dir = _pkg_dir / "ui" / "static"
if frontend_dir.exists():
    app.mount("/frontend", StaticFiles(directory=str(frontend_dir)), name="frontend")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(str(frontend_dir / "index.html"))
