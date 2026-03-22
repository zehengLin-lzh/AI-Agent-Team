"""Agent Team Backend -- FastAPI application."""
import json
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from agent_team.config import MODEL, OLLAMA_BASE_URL, REPO_ROOT
from agent_team.agents.runner import AgentTeam
from agent_team.ollama.client import get_active_model, set_active_model
from agent_team.agents.http_runner import (
    AskMode, AskRequest, AskResponse, run_team_http,
)
from agent_team.agents.definitions import AgentMode
from agent_team.plans.storage import save_plan_markdown

app = FastAPI(title="Agent Team v2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        data = json.loads(raw)
        if data.get("type") == "start":
            team = AgentTeam(websocket, execution_path=data.get("execution_path"))
            mode = data.get("mode", "coding")
            await team.run(data["content"], mode=mode)
    except WebSocketDisconnect:
        pass


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
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

    phase_outputs = await run_team_http(
        user_plan_with_context, mode,
        agent_mode=agent_mode, execution_path=execution_path,
    )

    planner_output = phase_outputs.get("PLANNER", "") or user_plan
    first_line = next((ln.strip() for ln in planner_output.splitlines() if ln.strip()), "")
    title = first_line[:100] if first_line else "Agent Team Plan"

    plan_path = save_plan_markdown(
        title=title, plan_text=planner_output,
        execution_path=execution_path, mode=agent_mode.value,
    )

    return AskResponse(
        title=title,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        mode=mode,
        agent_mode=agent_mode.value,
        execution_path=execution_path,
        plan_file_path=str(plan_path),
        phase_outputs=phase_outputs,
    )


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
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
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            data = r.json()
            data["active_model"] = get_active_model()
            return data
    except Exception as e:
        return {"error": str(e)}


@app.post("/models/switch")
async def switch_model(body: dict):
    model_name = body.get("model", "").strip()
    if not model_name:
        return {"error": "model name required"}
    # Verify model exists in Ollama
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            available = [m["name"] for m in r.json().get("models", [])]
            if not any(model_name in m for m in available):
                return {"error": f"Model '{model_name}' not found", "available": available}
    except Exception as e:
        return {"error": f"Cannot reach Ollama: {e}"}
    set_active_model(model_name)
    return {"status": "ok", "active_model": model_name}


# Serve frontend static files
_pkg_dir = Path(__file__).resolve().parent.parent
frontend_dir = _pkg_dir / "ui" / "static"
if frontend_dir.exists():
    app.mount("/frontend", StaticFiles(directory=str(frontend_dir)), name="frontend")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(str(frontend_dir / "index.html"))
