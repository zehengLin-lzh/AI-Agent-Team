"""HTTP (non-streaming) agent runner for the /ask endpoint."""
import asyncio
from enum import Enum
from pydantic import BaseModel

from agent_team.agents.definitions import (
    AgentMode, MODE_PHASE_ORDER, MODE_TEMPERATURES,
    get_agent_prompt, CONTEXT_AGENTS,
)
from agent_team.agents.context import build_context_for_agent
from agent_team.llm import call_llm
from agent_team.files.writer import extract_and_write_files
from agent_team.files.scaffolder import scaffold_plan_paths


class AskMode(str, Enum):
    PLAN_ONLY = "plan_only"
    PLAN_AND_EXECUTE = "plan_and_execute"


class AskRequest(BaseModel):
    plan: str
    mode: AskMode | None = None
    agent_mode: str | None = None  # thinking/coding/brainstorming/architecture/execution
    execution_path: str | None = None


class AskResponse(BaseModel):
    title: str
    timestamp: str
    mode: AskMode
    agent_mode: str
    execution_path: str | None
    plan_file_path: str
    phase_outputs: dict[str, str]


async def _run_agent_http(
    agent_name: str,
    original_plan: str,
    phase_outputs: dict[str, str],
    agent_mode: AgentMode = AgentMode.CODING,
) -> str:
    system_prompt = get_agent_prompt(agent_name, agent_mode)
    temperature = MODE_TEMPERATURES.get(agent_mode, 0.3)
    messages = build_context_for_agent(
        agent_name, phase_outputs, original_plan,
    )
    messages.append({
        "role": "user",
        "content": f"Please proceed as {agent_name}.",
    })
    content = await call_llm(
        system_prompt=system_prompt,
        messages=messages,
        temperature=temperature,
    )
    phase_outputs[agent_name] = content
    return content


async def run_team_http(
    user_plan: str,
    mode: AskMode,
    agent_mode: AgentMode = AgentMode.CODING,
    execution_path: str | None = None,
) -> dict[str, str]:
    phase_outputs: dict[str, str] = {}
    phase_order = MODE_PHASE_ORDER.get(agent_mode, MODE_PHASE_ORDER[AgentMode.CODING])

    for phase_group in phase_order:
        if mode == AskMode.PLAN_ONLY and phase_group[0] in ("EXECUTOR",):
            continue

        if len(phase_group) == 1:
            agent = phase_group[0]
            output = await _run_agent_http(agent, user_plan, phase_outputs, agent_mode)
            # Handle scaffolding after PLANNER
            if agent == "PLANNER":
                scaffold_plan_paths(output, execution_path=execution_path)
            # Handle file writing after EXECUTOR
            if agent == "EXECUTOR" and mode != AskMode.PLAN_ONLY:
                extract_and_write_files(output, execution_path=execution_path)
        else:
            await asyncio.gather(
                *[_run_agent_http(a, user_plan, phase_outputs, agent_mode) for a in phase_group]
            )

    return phase_outputs
