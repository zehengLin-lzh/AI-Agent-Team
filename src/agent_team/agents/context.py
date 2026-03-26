"""Context building for agents with token budget management."""
from agent_team.agents.definitions import CONTEXT_AGENTS, STAGE_CONTEXT, AGENT_REGISTRY_MAP


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def build_pattern_context(patterns: list[dict], max_tokens: int = 1500) -> str:
    """Format learned patterns for injection into agent prompts."""
    if not patterns:
        return ""
    lines = ["## Lessons from Past Sessions (avoid these known mistakes):"]
    for p in patterns:
        conf = p.get("confidence", 0.5)
        desc = p.get("description", "")
        cat = p.get("category", "unknown")
        lines.append(f"- [{cat}] ({conf:.0%} confidence) {desc}")
    text = "\n".join(lines)
    return truncate_to_tokens(text, max_tokens)


def build_context_for_agent(
    agent_name: str,
    phase_outputs: dict[str, str],
    original_plan: str,
    memory_context: str = "",
    patterns_context: str = "",
    intra_stage_outputs: dict[str, str] | None = None,
    max_tokens: int = 24000,
) -> list[dict]:
    """Build the message history an agent should see, with token budgeting.

    For legacy agents (ORCHESTRATOR, THINKER, etc.), uses CONTEXT_AGENTS.
    For named agents (ORCH_LUMUSI, THINK_SOREN, etc.), uses STAGE_CONTEXT
    for prior-stage outputs and optionally intra_stage_outputs for
    within-stage colleague perspectives.
    """
    messages = []
    token_count = 0

    # Priority 1: Original plan (always included)
    plan_tokens = estimate_tokens(original_plan)
    messages.append({"role": "user", "content": original_plan})
    token_count += plan_tokens

    # Priority 2: Memory/session context (capped at 35% of budget)
    if memory_context:
        mem_tokens = estimate_tokens(memory_context)
        max_mem = int(max_tokens * 0.35)
        if mem_tokens > max_mem:
            memory_context = truncate_to_tokens(memory_context, max_mem)
            mem_tokens = max_mem
        if token_count + mem_tokens < max_tokens:
            label = "IMPORTANT CONTEXT — reference specific files, functions, and patterns below:"
            if "## Repository Context" in memory_context or "## Directory Structure" in memory_context:
                label = "REPOSITORY SCAN RESULTS — you MUST reference specific files and code from this context in your analysis:"
            messages.append({
                "role": "system",
                "content": f"{label}\n{memory_context}",
            })
            token_count += mem_tokens

    # Priority 2.5: Learned patterns (capped at ~6% of budget)
    if patterns_context:
        pat_tokens = estimate_tokens(patterns_context)
        max_pat = int(max_tokens * 0.06)
        if pat_tokens > max_pat:
            patterns_context = truncate_to_tokens(patterns_context, max_pat)
            pat_tokens = max_pat
        if token_count + pat_tokens < max_tokens:
            messages.append({
                "role": "system",
                "content": patterns_context,
            })
            token_count += pat_tokens

    # Priority 3: Prior outputs — use stage context for named agents, legacy for others
    spec = AGENT_REGISTRY_MAP.get(agent_name)
    if spec:
        # Named agent: use STAGE_CONTEXT for synthesized prior-stage outputs
        prior_keys = STAGE_CONTEXT.get(spec.stage, [])
    else:
        # Legacy agent: use CONTEXT_AGENTS
        prior_keys = CONTEXT_AGENTS.get(agent_name, [])

    for prior_key in prior_keys:
        if prior_key in phase_outputs:
            output = phase_outputs[prior_key]
            output_tokens = estimate_tokens(output)
            remaining = max_tokens - token_count
            if remaining <= 0:
                break
            if output_tokens > remaining:
                output = truncate_to_tokens(output, remaining)
            messages.append({"role": "assistant", "content": output})
            token_count += estimate_tokens(output)

    # Priority 4: Intra-stage outputs (other agents in same stage)
    if intra_stage_outputs:
        for colleague_id, colleague_output in intra_stage_outputs.items():
            if colleague_id == agent_name:
                continue
            remaining = max_tokens - token_count
            if remaining <= 200:
                break
            colleague_spec = AGENT_REGISTRY_MAP.get(colleague_id)
            label = colleague_spec.name if colleague_spec else colleague_id
            content = f"[{label}'s perspective]:\n{colleague_output}"
            output_tokens = estimate_tokens(content)
            if output_tokens > remaining:
                content = truncate_to_tokens(content, remaining)
            messages.append({"role": "assistant", "content": content})
            token_count += estimate_tokens(content)

    return messages
