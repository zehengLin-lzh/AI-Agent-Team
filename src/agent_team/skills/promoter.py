"""Convert LearnedPattern → Skill candidate using LLM rewriting.

When a high-confidence pattern is extracted from a session (typically from
an error-fix loop), we ask the LLM to reshape it into a reusable, imperative
skill instruction and emit a Skill dataclass. The caller is responsible for
writing the Skill to ``skills/pending/`` — this module only produces the
candidate, it does not touch disk.
"""
from __future__ import annotations

import logging
import re

from agent_team.memory.types import LearnedPattern
from agent_team.skills.types import Skill

logger = logging.getLogger(__name__)


PROMOTION_SYSTEM_PROMPT = """You convert debugging patterns into reusable skills.

Given a LearnedPattern (category + description), rewrite it as:
1. A short skill name (3-6 words, imperative)
2. A one-line description (what problem it solves)
3. A body of instructions (imperative voice, <200 words) that tells a future
   agent what to do/avoid. Focus on the rule, not the past incident.

Output EXACTLY this format, no extra prose:

NAME: <skill name>
DESCRIPTION: <one-line description>
INSTRUCTIONS:
<body>

Rules:
- Skill name is a concise phrase, not a sentence
- Do not reference "last session" or "previously" — skills are timeless
- Do not invent details not present in the pattern
- If the pattern is too vague to be reusable, output only: SKIP: <reason>
"""


# Mapping from pattern category → the agents that most benefit from the skill.
_CATEGORY_TO_AGENTS: dict[str, list[str]] = {
    "error_fix": ["EXECUTOR", "EXEC_KAI", "EXEC_DEV", "REVIEWER", "REV_QUINN"],
    "import_error": ["EXECUTOR", "EXEC_KAI", "EXEC_DEV"],
    "logic_error": ["EXECUTOR", "EXEC_KAI", "REVIEWER", "REV_QUINN"],
    "missing_file": ["EXECUTOR", "EXEC_KAI", "EXEC_DEV"],
    "wrong_api": ["EXECUTOR", "EXEC_KAI", "PLANNER", "PLAN_ATLAS"],
    "naming_inconsistency": ["EXECUTOR", "REVIEWER", "REV_QUINN"],
    "format_error": ["EXECUTOR", "REVIEWER"],
    "security_issue": ["EXECUTOR", "REVIEWER", "REV_QUINN"],
    "coding_pattern": ["EXECUTOR", "EXEC_KAI", "EXEC_DEV"],
    "architecture_pattern": ["THINKER", "THINK_SOREN", "PLANNER", "PLAN_ATLAS"],
    "best_practice": ["THINKER", "PLANNER", "EXECUTOR", "REVIEWER"],
    "preference": ["THINKER", "PLANNER", "EXECUTOR", "REVIEWER"],
}

DEFAULT_AGENTS = ["THINKER", "PLANNER", "EXECUTOR", "REVIEWER"]


def _parse_promotion(raw: str) -> tuple[str, str, str] | None:
    """Parse LLM output. Returns (name, description, instructions) or None."""
    if re.search(r"^\s*SKIP\s*:", raw, re.MULTILINE):
        return None

    name_m = re.search(r"^NAME:\s*(.+)$", raw, re.MULTILINE)
    desc_m = re.search(r"^DESCRIPTION:\s*(.+)$", raw, re.MULTILINE)
    instr_m = re.search(r"^INSTRUCTIONS:\s*\n(.*)", raw, re.DOTALL | re.MULTILINE)

    if not (name_m and desc_m and instr_m):
        return None

    name = name_m.group(1).strip().strip('"').strip("'")
    description = desc_m.group(1).strip().strip('"').strip("'")
    instructions = instr_m.group(1).strip()

    if not name or not instructions:
        return None

    return name, description, instructions


def _agents_for_category(category: str) -> list[str]:
    return _CATEGORY_TO_AGENTS.get(category, DEFAULT_AGENTS)


async def promote_pattern_to_skill(
    pattern: LearnedPattern,
    *,
    llm_caller=None,
) -> Skill | None:
    """Ask the LLM to convert a pattern into a Skill candidate.

    ``llm_caller`` should be an async callable matching
    ``call_llm(system_prompt=..., messages=..., temperature=...)``.
    Defaults to ``agent_team.llm.call_llm`` but can be injected for tests.
    Returns None if the LLM declines (SKIP) or parsing fails.
    """
    if llm_caller is None:
        from agent_team.llm import call_llm
        llm_caller = call_llm

    user_msg = (
        f"Pattern category: {pattern.category}\n"
        f"Pattern description: {pattern.description}\n"
        f"Confidence: {pattern.confidence:.2f}"
    )

    try:
        raw = await llm_caller(
            system_prompt=PROMOTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            temperature=0.2,
        )
    except Exception as e:
        logger.debug(f"Skill promotion LLM call failed: {e}")
        return None

    parsed = _parse_promotion(raw or "")
    if not parsed:
        return None

    name, description, instructions = parsed
    return Skill(
        name=name,
        description=description,
        mode="all",
        instructions=instructions,
        allowed_agents=_agents_for_category(pattern.category),
        requires={"source_pattern_id": pattern.id},
    )
