"""Context building for agents with token budget management."""
from agent_team.agents.definitions import CONTEXT_AGENTS


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def build_context_for_agent(
    agent_name: str,
    phase_outputs: dict[str, str],
    original_plan: str,
    memory_context: str = "",
    max_tokens: int = 24000,
) -> list[dict]:
    """Build the message history an agent should see, with token budgeting."""
    messages = []
    token_count = 0

    # Priority 1: Original plan (always included)
    plan_tokens = estimate_tokens(original_plan)
    messages.append({"role": "user", "content": original_plan})
    token_count += plan_tokens

    # Priority 2: Memory context (capped at 30% of budget)
    if memory_context:
        mem_tokens = estimate_tokens(memory_context)
        max_mem = int(max_tokens * 0.3)
        if mem_tokens > max_mem:
            memory_context = truncate_to_tokens(memory_context, max_mem)
            mem_tokens = max_mem
        if token_count + mem_tokens < max_tokens:
            messages.append({
                "role": "system",
                "content": f"Relevant knowledge from past sessions:\n{memory_context}",
            })
            token_count += mem_tokens

    # Priority 3: Prior agent outputs (most recent first, truncate if needed)
    for prior_agent in CONTEXT_AGENTS.get(agent_name, []):
        if prior_agent in phase_outputs:
            output = phase_outputs[prior_agent]
            output_tokens = estimate_tokens(output)
            remaining = max_tokens - token_count
            if remaining <= 0:
                break
            if output_tokens > remaining:
                output = truncate_to_tokens(output, remaining)
            messages.append({"role": "assistant", "content": output})
            token_count += estimate_tokens(output)

    return messages
