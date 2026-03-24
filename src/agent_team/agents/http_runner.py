"""HTTP (non-streaming) agent runner for the /ask endpoint."""
import asyncio
import re
from enum import Enum
from pydantic import BaseModel

from agent_team.config import MODEL_ROUTING, SIMPLE_MODEL_ROUTING, MAX_FIX_LOOPS
from agent_team.agents.definitions import (
    AgentMode, MODE_PHASE_ORDER, SIMPLE_PHASE_ORDER, MODE_TEMPERATURES,
    get_agent_prompt, CONTEXT_AGENTS,
    DEBATE_CHALLENGER_PROMPT, DEBATE_RESPONSE_PROMPT,
)
from agent_team.agents.context import build_context_for_agent, build_pattern_context
from agent_team.agents.complexity import TaskComplexity, classify_complexity
from agent_team.llm import call_llm, get_active_model, set_active_model
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
    file_changes: list[dict] | None = None  # Rich file change data with diffs


def _swap_model(agent_name: str, complexity: str = "medium") -> tuple[str | None, bool]:
    """Swap to routed model. Returns (original, did_swap)."""
    if complexity == "simple":
        routed = SIMPLE_MODEL_ROUTING.get(agent_name) or MODEL_ROUTING.get(agent_name)
    else:
        routed = MODEL_ROUTING.get(agent_name)
    if not routed:
        return None, False
    try:
        original = get_active_model()
        if routed != original:
            set_active_model(routed)
            return original, True
    except Exception:
        pass
    return None, False


def _restore_model(original: str | None, did_swap: bool):
    if did_swap and original:
        try:
            set_active_model(original)
        except Exception:
            pass


async def _run_agent_http(
    agent_name: str,
    original_plan: str,
    phase_outputs: dict[str, str],
    agent_mode: AgentMode = AgentMode.CODING,
    complexity: str = "medium",
    patterns_context: str = "",
) -> str:
    system_prompt = get_agent_prompt(agent_name, agent_mode, complexity=complexity)
    temperature = MODE_TEMPERATURES.get(agent_mode, 0.3)
    messages = build_context_for_agent(
        agent_name, phase_outputs, original_plan,
        patterns_context=patterns_context,
    )
    messages.append({
        "role": "user",
        "content": f"Please proceed as {agent_name}.",
    })

    # Model routing
    original_model, did_swap = _swap_model(agent_name, complexity)
    try:
        content = await call_llm(
            system_prompt=system_prompt,
            messages=messages,
            temperature=temperature,
        )
    finally:
        _restore_model(original_model, did_swap)

    phase_outputs[agent_name] = content
    return content


async def _run_debate_http(
    original_plan: str,
    phase_outputs: dict[str, str],
    agent_mode: AgentMode,
    complexity: str = "medium",
):
    """Run challenger debate after THINKER."""
    thinker_output = phase_outputs.get("THINKER", "")
    if not thinker_output:
        return

    temperature = MODE_TEMPERATURES.get(agent_mode, 0.3)

    # CHALLENGER
    original_model, did_swap = _swap_model("CHALLENGER", complexity)
    try:
        challenge_messages = [
            {"role": "user", "content": original_plan},
            {"role": "assistant", "content": thinker_output},
            {"role": "user", "content": "Please critically review the above analysis. Find weaknesses, gaps, and suggest improvements."},
        ]
        challenger_output = await call_llm(
            system_prompt=DEBATE_CHALLENGER_PROMPT,
            messages=challenge_messages,
            temperature=temperature + 0.1,
        )
        phase_outputs["CHALLENGER"] = challenger_output
    finally:
        _restore_model(original_model, did_swap)

    # THINKER_REFINED
    original_model2, did_swap2 = _swap_model("THINKER_REFINED", complexity)
    try:
        response_messages = [
            {"role": "user", "content": original_plan},
            {"role": "assistant", "content": thinker_output},
            {"role": "user", "content": f"A critical reviewer has raised these challenges:\n\n{challenger_output}\n\nPlease respond to each challenge and produce a refined analysis."},
        ]
        refined = await call_llm(
            system_prompt=DEBATE_RESPONSE_PROMPT,
            messages=response_messages,
            temperature=temperature,
        )
        phase_outputs["THINKER"] = refined  # Replace with refined
    finally:
        _restore_model(original_model2, did_swap2)


def _needs_fix(output: str) -> list[str]:
    """Check reviewer output for fix markers."""
    fixes = []
    for marker in ("FIX_REQUIRED:", "REVISION_REQUIRED:"):
        match = re.search(rf"{marker}\n(.*?)(?:\n\u2192|\Z)", output, re.DOTALL)
        if match:
            for line in match.group(1).strip().split("\n"):
                line = line.strip().lstrip("- ")
                if line:
                    fixes.append(line)
    return fixes


async def run_team_http(
    user_plan: str,
    mode: AskMode,
    agent_mode: AgentMode = AgentMode.CODING,
    execution_path: str | None = None,
) -> dict[str, str]:
    phase_outputs: dict[str, str] = {}

    # Classify complexity for adaptive routing
    complexity = classify_complexity(user_plan, agent_mode.value)

    # Query learned patterns for injection
    patterns_context = ""
    injected_ids: list[str] = []
    try:
        from agent_team.memory.database import MemoryDB
        _db = MemoryDB()
        patterns = _db.get_relevant_patterns(min_confidence=0.4, limit=10)
        if patterns:
            patterns_context = build_pattern_context(patterns)
            injected_ids = [p["id"] for p in patterns]
        _db.close()
    except Exception:
        pass

    # Select phase order based on complexity
    if complexity == TaskComplexity.SIMPLE:
        phase_order = SIMPLE_PHASE_ORDER.get(agent_mode, SIMPLE_PHASE_ORDER[AgentMode.CODING])
    else:
        phase_order = MODE_PHASE_ORDER.get(agent_mode, MODE_PHASE_ORDER[AgentMode.CODING])

    fix_count = 0
    file_changes_data: list[dict] = []

    for phase_group in phase_order:
        if mode == AskMode.PLAN_ONLY and phase_group[0] in ("EXECUTOR",):
            continue

        if len(phase_group) == 1:
            agent = phase_group[0]
            output = await _run_agent_http(
                agent, user_plan, phase_outputs, agent_mode,
                complexity=complexity.value, patterns_context=patterns_context,
            )

            # Run debate after THINKER (skip for simple tasks)
            if agent == "THINKER" and complexity != TaskComplexity.SIMPLE:
                await _run_debate_http(
                    user_plan, phase_outputs, agent_mode,
                    complexity=complexity.value,
                )

            # Handle scaffolding after PLANNER
            if agent == "PLANNER":
                scaffold_plan_paths(output, execution_path=execution_path)

            # Handle file writing after EXECUTOR
            if agent == "EXECUTOR" and mode != AskMode.PLAN_ONLY:
                planner_out = phase_outputs.get("PLANNER", "")
                changes = extract_and_write_files(
                    output, execution_path=execution_path,
                    planner_output=planner_out,
                )
                if changes:
                    file_changes_data = [
                        {
                            "path": str(c.path),
                            "is_new": c.is_new,
                            "diff": c.diff,
                            "preview": c.preview,
                        }
                        for c in changes
                    ]

            # Fix loop after REVIEWER
            if agent == "REVIEWER" and agent_mode in (AgentMode.CODING, AgentMode.EXECUTION):
                fixes = _needs_fix(output)
                while fixes and fix_count < MAX_FIX_LOOPS:
                    fix_count += 1
                    original_executor = phase_outputs.get("EXECUTOR", "")
                    fix_context = "FIXES NEEDED:\n" + "\n".join(f"- {f}" for f in fixes)
                    phase_outputs["EXECUTOR"] = original_executor + f"\n\n{fix_context}"
                    # Re-run executor
                    exec_output = await _run_agent_http(
                        "EXECUTOR", user_plan, phase_outputs, agent_mode,
                        complexity=complexity.value, patterns_context=patterns_context,
                    )
                    if mode != AskMode.PLAN_ONLY:
                        changes = extract_and_write_files(
                            exec_output, execution_path=execution_path,
                            planner_output=planner_out,
                        )
                        if changes:
                            file_changes_data = [
                                {"path": str(c.path), "is_new": c.is_new, "diff": c.diff, "preview": c.preview}
                                for c in changes
                            ]
                    # Extract error patterns from fix loop (best-effort)
                    try:
                        from agent_team.learning.extractor import extract_error_patterns
                        await extract_error_patterns(
                            reviewer_output=output,
                            executor_original=original_executor,
                            executor_fixed=phase_outputs.get("EXECUTOR", ""),
                            user_plan=user_plan,
                        )
                    except Exception:
                        pass
                    # Re-run reviewer
                    output = await _run_agent_http(
                        "REVIEWER", user_plan, phase_outputs, agent_mode,
                        complexity=complexity.value, patterns_context=patterns_context,
                    )
                    fixes = _needs_fix(output)
        else:
            await asyncio.gather(
                *[
                    _run_agent_http(
                        a, user_plan, phase_outputs, agent_mode,
                        complexity=complexity.value, patterns_context=patterns_context,
                    )
                    for a in phase_group
                ]
            )

    # Boost/decay injected patterns based on outcome
    try:
        if injected_ids:
            from agent_team.memory.database import MemoryDB
            _db = MemoryDB()
            had_fixes = fix_count > 0
            delta = 0.05 if not had_fixes else -0.05
            for pid in injected_ids:
                _db.boost_pattern_confidence(pid, delta)
            _db.close()
    except Exception:
        pass

    # Post-session learning
    try:
        from agent_team.learning.extractor import extract_session_knowledge
        await extract_session_knowledge(
            user_plan=user_plan,
            mode=agent_mode.value,
            phase_outputs=phase_outputs,
        )
    except Exception:
        pass

    # Attach file changes to outputs for API consumers
    if file_changes_data:
        phase_outputs["_file_changes"] = file_changes_data  # type: ignore[assignment]

    return phase_outputs
