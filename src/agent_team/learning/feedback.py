"""User feedback detection and extraction.

Detects corrections, preferences, and explicit memory commands from user
messages, then stores them as high-priority feedback entries.
"""
from __future__ import annotations

import json
import re
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_team.memory.database import MemoryDB

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """Analyze the user message below. Did the user correct a mistake,
express a preference, criticize an approach, or explicitly say
"remember", "don't", "always", "never", or similar directive language?

Respond ONLY as JSON (no other text):
{{"is_feedback": true/false, "rule": "imperative one-liner if feedback", "rationale": "brief why", "category": "coding|style|tone|tools|other"}}

If is_feedback is false, respond: {{"is_feedback": false}}

User message:
<<<{user_message}>>>"""

# Confidence tiers by trigger source
CONFIDENCE_TIERS = {
    "slash": 1.0,       # /remember — user explicitly said it
    "learn-this": 0.95,  # /learn-this — user confirmed it
    "auto": 0.85,        # auto-detected — may be wrong
}

MAX_AUTO_PER_SESSION = 3  # Cap auto-detected feedback per session


def _parse_judge_response(raw: str) -> dict | None:
    """Extract JSON from LLM judge response, handling markdown fences."""
    # Try to find JSON in the response
    # Handle ```json ... ``` blocks
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if json_match:
        raw = json_match.group(1)
    else:
        # Try to find bare JSON object
        json_match = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(0)

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "is_feedback" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


async def detect_feedback(user_msg: str, llm_provider) -> dict | None:
    """Use the active LLM to judge if a user message contains feedback.

    Returns parsed dict with {is_feedback, rule, rationale, category} or None on failure.
    """
    # Skip conditions
    if not user_msg or len(user_msg.strip()) < 15:
        return None
    if user_msg.strip().startswith("/"):
        return None
    # Skip pure code blocks
    stripped = user_msg.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return None

    prompt = JUDGE_PROMPT.format(user_message=user_msg[:500])  # cap input

    try:
        response = await llm_provider.call(
            system_prompt="You are a concise JSON-only classifier.",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        result = _parse_judge_response(response)
        if result and result.get("is_feedback"):
            return result
        return None
    except Exception as e:
        logger.debug(f"Feedback detection failed: {e}")
        return None


async def extract_and_store(
    user_msg: str,
    session_id: str | None,
    trigger: str = "auto",
    db: MemoryDB | None = None,
    llm_provider=None,
    rule: str | None = None,
    rationale: str | None = None,
    category: str | None = None,
) -> str | None:
    """Extract feedback from a user message and store it.

    For trigger='slash', rule should be provided directly (from /remember text).
    For trigger='auto', uses the LLM judge to extract rule/rationale/category.
    For trigger='learn-this', uses a variant prompt with the last assistant message.

    Returns the feedback ID if stored, None otherwise.
    """
    if db is None:
        from agent_team.memory.database import MemoryDB
        db = MemoryDB()

    confidence = CONFIDENCE_TIERS.get(trigger, 0.85)

    # For slash commands, the user provided the rule directly
    if trigger == "slash" and rule:
        feedback_id = db.create_feedback(
            rule=rule,
            rationale=rationale or "User explicitly asked to remember this",
            trigger=trigger,
            source_session_id=session_id,
            source_message=user_msg[:500],
            category=category,
            confidence=confidence,
        )
        return feedback_id

    # For auto/learn-this, use LLM to extract
    if llm_provider is None:
        return None

    result = await detect_feedback(user_msg, llm_provider)
    if result and result.get("is_feedback"):
        feedback_id = db.create_feedback(
            rule=result.get("rule", user_msg[:100]),
            rationale=result.get("rationale", ""),
            trigger=trigger,
            source_session_id=session_id,
            source_message=user_msg[:500],
            category=result.get("category", "other"),
            confidence=confidence,
        )
        return feedback_id

    return None
