"""Intent classification for CLI auto-routing.

Replaces the old ``/ask`` / ``/chat`` / ``/plan`` / ``/exec`` slash commands.
Given a bare user message, returns one of three intents:

- ``CONVERSATION`` — greetings, thanks, short continuations. Direct LLM reply,
  session history in, no tools.
- ``QUERY`` — factual / informational question. Direct LLM reply with web
  search enabled (if the MCP tool is available).
- ``TASK`` — imperative work. Full multi-agent pipeline: plan → confirm →
  execute.

The classifier is a two-stage hybrid:

1. **Fast path** — regex/keyword ladder. Covers the large majority of obvious
   inputs in ~1 ms.
2. **Slow path** — one ``FAST_MODEL`` LLM call with a few-shot prompt and a
   small slice of prior history. Only fires when the fast path's confidence
   is below ``FAST_PATH_THRESHOLD``.

On any LLM failure the classifier degrades gracefully: question-mark → QUERY,
otherwise → TASK in plan_only mode. It never silently routes an ambiguous
query into a multi-agent pipeline.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

from agent_team.agents.complexity import (
    TaskClassification,
    classify_task,
    is_question_query,
)

if TYPE_CHECKING:
    from agent_team.agents.session import SessionContext

logger = logging.getLogger(__name__)

FAST_PATH_THRESHOLD = 0.7


class Intent(str, Enum):
    CONVERSATION = "conversation"
    QUERY = "query"
    TASK = "task"


@dataclass
class IntentClassification:
    intent: Intent
    confidence: float = 0.5
    reason: str = ""
    source: str = "fast"  # "fast" | "llm" | "fallback"
    needs_web: bool = False
    task_classification: TaskClassification | None = None

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "source": self.source,
            "needs_web": self.needs_web,
        }


# ── Fast-path signals ──────────────────────────────────────────────────────

_TASK_VERBS = re.compile(
    r"^\s*(fix|add|build|write|implement|refactor|debug|test|deploy|create|"
    r"update|delete|install|run|execute|generate|convert|migrate|rename|"
    r"optimize|remove|support|setup|set up|integrate|configure|修|写|实现|"
    r"重构|加|增加|删除|部署|执行|配置)\b",
    re.IGNORECASE,
)

_PATH_LIKE = re.compile(
    r"(?:\b\w+\.(?:py|js|ts|jsx|tsx|java|go|rs|rb|yaml|yml|json|toml|cfg|ini|sql|md|sh)\b|"
    r"(?:^|[\s(])/[\w/.-]+|"
    r"(?:^|[\s(])[\w.-]+/[\w/.-]+)",
    re.IGNORECASE,
)

_MULTI_STEP = re.compile(
    r"\b(then|after that|also|and also|additionally|furthermore|next step|"
    r"on top of|as well as|followed by|然后|接着|另外|还要|同时)\b",
    re.IGNORECASE,
)

_QUERY_TIMELINESS = re.compile(
    r"\b(latest|current|newest|recent|now|today|最新|现在|目前|当前)\b",
    re.IGNORECASE,
)

_QUERY_REFERENCE = re.compile(
    r"\b(docs?|documentation|version|release|reference|spec|tutorial|api|"
    r"文档|版本|教程|参考|规范)\b",
    re.IGNORECASE,
)

_GREETING = re.compile(
    r"^(\s*)(hi|hello|hey|yo|sup|thanks?|thank you|cool|ok|okay|nice|great|"
    r"got it|sure|sounds good|alright|bye|goodbye|gotcha|"
    r"你好|嗨|谢谢|多谢|好的|收到|明白|行|ok 了|没问题|再见|好嘞)\b[\s.!?。！？]*$",
    re.IGNORECASE,
)

_SHORT_CONTINUATION = re.compile(
    r"^(\s*)(and|also|but|so|why|explain|elaborate|more|continue|go on|"
    r"再|还|然后|继续|详细说|为什么|再展开|展开)\b",
    re.IGNORECASE,
)


def _fast_classify(user_input: str) -> IntentClassification:
    """Stage-1 regex classifier. Always returns; confidence signals quality."""
    text = user_input.strip()
    word_count = len(text.split())

    if not text:
        return IntentClassification(
            intent=Intent.CONVERSATION,
            confidence=0.9,
            reason="empty input",
            source="fast",
        )

    # CONVERSATION: short + greeting/acknowledgement pattern
    if _GREETING.match(text):
        return IntentClassification(
            intent=Intent.CONVERSATION,
            confidence=0.95,
            reason="greeting/acknowledgement",
            source="fast",
        )
    if word_count <= 3 and not is_question_query(text) and not _TASK_VERBS.match(text):
        return IntentClassification(
            intent=Intent.CONVERSATION,
            confidence=0.8,
            reason="very short non-question, non-imperative",
            source="fast",
        )

    # TASK: command verb at the start, or file path reference, or multi-step
    task_verb_hit = bool(_TASK_VERBS.match(text))
    path_hit = bool(_PATH_LIKE.search(text))
    multi_step_hit = bool(_MULTI_STEP.search(text))
    if task_verb_hit and not is_question_query(text):
        # Verb like "fix X" or "write Y" — high confidence TASK.
        return IntentClassification(
            intent=Intent.TASK,
            confidence=0.9 if path_hit or multi_step_hit else 0.8,
            reason="leads with task verb",
            source="fast",
            task_classification=classify_task(text),
        )
    if path_hit and not is_question_query(text) and word_count > 3:
        return IntentClassification(
            intent=Intent.TASK,
            confidence=0.75,
            reason="contains file path reference",
            source="fast",
            task_classification=classify_task(text),
        )

    # QUERY: question-form (covers ?, leading question words, CJK equivalents)
    if is_question_query(text):
        needs_web = bool(
            _QUERY_TIMELINESS.search(text) or _QUERY_REFERENCE.search(text)
        )
        return IntentClassification(
            intent=Intent.QUERY,
            confidence=0.9 if needs_web else 0.8,
            reason="question form",
            source="fast",
            needs_web=needs_web,
        )

    # Short continuation cues without question form or verb — CONVERSATION.
    if word_count <= 6 and _SHORT_CONTINUATION.match(text):
        return IntentClassification(
            intent=Intent.CONVERSATION,
            confidence=0.7,
            reason="short continuation cue",
            source="fast",
        )

    # Ambiguous — stage-2 LLM gets the call.
    return IntentClassification(
        intent=Intent.TASK,  # tentative best guess
        confidence=0.4,
        reason="no strong fast-path signal",
        source="fast",
    )


# ── Slow-path LLM classifier ──────────────────────────────────────────────

_LLM_SYSTEM_PROMPT = (
    "You classify a user's chat message as exactly one of: "
    "CONVERSATION, QUERY, or TASK.\n\n"
    "- CONVERSATION: greetings, thanks, short reactions, or casual follow-ups "
    "that don't ask for new information or work (e.g. 'thanks', 'explain that', 'ok').\n"
    "- QUERY: a request for facts or explanation (e.g. 'what is Docker', "
    "'latest Airflow version', 'how does async/await work').\n"
    "- TASK: an imperative instruction to produce code, edit files, or perform "
    "multi-step engineering work (e.g. 'fix the null check in foo.py', "
    "'write a Fibonacci function', 'refactor the auth module').\n\n"
    "Reply with JSON only, no prose: "
    '{"intent": "CONVERSATION|QUERY|TASK", '
    '"confidence": 0.0-1.0, '
    '"needs_web": true|false, '
    '"reason": "one short phrase"}.'
)


def _parse_llm_response(raw: str) -> IntentClassification | None:
    """Extract the JSON verdict from the LLM's reply."""
    if not raw:
        return None
    match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None

    raw_intent = str(data.get("intent", "")).upper().strip()
    try:
        intent = Intent[raw_intent]
    except KeyError:
        return None

    try:
        confidence = float(data.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))

    return IntentClassification(
        intent=intent,
        confidence=confidence,
        reason=str(data.get("reason", ""))[:120],
        source="llm",
        needs_web=bool(data.get("needs_web", False)),
    )


def _recent_context(session: SessionContext | None, turns: int = 3) -> str:
    """Serialize the last few messages for continuation disambiguation."""
    if not session or not session.messages:
        return ""
    tail = session.messages[-turns * 2:]
    parts: list[str] = []
    for msg in tail:
        content = msg.content[:160].replace("\n", " ")
        parts.append(f"{msg.role}: {content}")
    return "\n".join(parts)


async def _slow_classify(
    user_input: str,
    *,
    session: SessionContext | None,
    llm_caller: Callable[..., Awaitable[str]],
) -> IntentClassification | None:
    """Stage-2 LLM classifier. Returns None on any failure."""
    context = _recent_context(session)
    prompt_body = f"Input: {user_input.strip()}"
    if context:
        prompt_body = f"Recent turns:\n{context}\n\n{prompt_body}"
    try:
        raw = await llm_caller(
            system_prompt=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt_body}],
            temperature=0.1,
        )
    except Exception as e:
        logger.debug("IntentRouter LLM call failed: %s", e)
        return None
    return _parse_llm_response(raw or "")


# ── Public API ────────────────────────────────────────────────────────────

async def classify_intent(
    user_input: str,
    *,
    session: SessionContext | None = None,
    llm_caller: Callable[..., Awaitable[str]] | None = None,
) -> IntentClassification:
    """Classify a user message into CONVERSATION / QUERY / TASK.

    ``llm_caller`` lets tests inject a stub. Defaults to
    ``agent_team.llm.call_llm``. The caller may be skipped entirely by passing
    a sentinel like ``lambda **_: ""`` — the classifier will fall back to the
    fast-path result if the slow path returns nothing.
    """
    fast = _fast_classify(user_input)
    if fast.confidence >= FAST_PATH_THRESHOLD:
        # Populate TaskClassification so TASK paths can route by complexity.
        if fast.intent == Intent.TASK and fast.task_classification is None:
            fast.task_classification = classify_task(user_input)
        return fast

    if llm_caller is None:
        from agent_team.llm import call_llm
        llm_caller = call_llm

    slow = await _slow_classify(user_input, session=session, llm_caller=llm_caller)
    if slow is not None:
        if slow.intent == Intent.TASK:
            slow.task_classification = classify_task(user_input)
        return slow

    # Both stages failed; degrade safely — never walk into a heavy pipeline silently.
    if is_question_query(user_input):
        return IntentClassification(
            intent=Intent.QUERY,
            confidence=0.5,
            reason="fallback: question-mark heuristic",
            source="fallback",
            needs_web=True,
        )
    return IntentClassification(
        intent=Intent.TASK,
        confidence=0.5,
        reason="fallback: default to plan_only",
        source="fallback",
        task_classification=classify_task(user_input),
    )
