"""Tests for the IntentRouter (CLI auto-routing replacement for /ask/chat/plan/exec)."""
from __future__ import annotations

import pytest

from agent_team.agents.complexity import TaskComplexity, is_question_query
from agent_team.agents.intent import (
    FAST_PATH_THRESHOLD,
    Intent,
    IntentClassification,
    _fast_classify,
    _parse_llm_response,
    _recent_context,
    classify_intent,
)
from agent_team.agents.session import SessionContext


# ── is_question_query (complexity fix) ──────────────────────────────

class TestQuestionDetection:
    def test_question_word_english(self):
        assert is_question_query("what is the latest version of airflow") is True

    def test_question_mark_ascii(self):
        assert is_question_query("does this compile?") is True

    def test_question_mark_fullwidth(self):
        assert is_question_query("这是什么？") is True

    def test_cjk_leader(self):
        assert is_question_query("什么是 Docker") is True

    def test_imperative_not_question(self):
        assert is_question_query("fix the null check in foo.py") is False

    def test_long_question_not_treated_as_simple(self):
        long_q = ("what is " + "very " * 25 + "long question")
        assert is_question_query(long_q) is False

    def test_empty_not_question(self):
        assert is_question_query("") is False


# ── _fast_classify ────────────────────────────────────────────────────

class TestFastPathTask:
    def test_leading_verb_fix(self):
        r = _fast_classify("fix the null check in foo.py")
        assert r.intent == Intent.TASK
        assert r.confidence >= 0.8

    def test_leading_verb_write(self):
        r = _fast_classify("write a Python fibonacci function")
        assert r.intent == Intent.TASK

    def test_chinese_imperative(self):
        r = _fast_classify("修一下 foo.py 里的 bug")
        assert r.intent == Intent.TASK

    def test_file_reference_without_leading_verb(self):
        r = _fast_classify("the bug is somewhere in src/foo.py")
        assert r.intent == Intent.TASK

    def test_task_cls_populated(self):
        r = _fast_classify("fix the null check in foo.py")
        assert r.task_classification is not None
        assert r.task_classification.complexity in TaskComplexity


class TestFastPathQuery:
    def test_english_question(self):
        r = _fast_classify("what is the latest version of airflow")
        assert r.intent == Intent.QUERY
        assert r.needs_web is True  # has "latest"
        assert r.confidence >= 0.8

    def test_question_no_timeliness(self):
        r = _fast_classify("what is async/await")
        assert r.intent == Intent.QUERY
        assert r.needs_web is False
        assert r.confidence >= 0.7

    def test_question_mark(self):
        r = _fast_classify("does async work in Python 3.11?")
        assert r.intent == Intent.QUERY

    def test_chinese_question(self):
        r = _fast_classify("什么是 Docker")
        assert r.intent == Intent.QUERY

    def test_query_with_version_keyword(self):
        r = _fast_classify("what version of airflow should I use")
        assert r.intent == Intent.QUERY
        assert r.needs_web is True


class TestFastPathConversation:
    def test_greeting(self):
        r = _fast_classify("hi")
        assert r.intent == Intent.CONVERSATION
        assert r.confidence >= 0.9

    def test_thanks(self):
        r = _fast_classify("thanks")
        assert r.intent == Intent.CONVERSATION

    def test_chinese_greeting(self):
        r = _fast_classify("你好")
        assert r.intent == Intent.CONVERSATION

    def test_chinese_thanks(self):
        r = _fast_classify("谢谢")
        assert r.intent == Intent.CONVERSATION

    def test_ok(self):
        r = _fast_classify("ok")
        assert r.intent == Intent.CONVERSATION

    def test_empty(self):
        r = _fast_classify("")
        assert r.intent == Intent.CONVERSATION

    def test_short_continuation(self):
        r = _fast_classify("explain that")
        assert r.intent == Intent.CONVERSATION


class TestFastPathAmbiguous:
    def test_medium_length_neither(self):
        # A vague statement with no clear signal — low confidence, triggers slow path.
        r = _fast_classify("that's an interesting thing to note here")
        assert r.confidence < FAST_PATH_THRESHOLD


# ── LLM slow-path parsing ───────────────────────────────────────────

class TestLLMResponseParsing:
    def test_parse_valid_task(self):
        raw = '{"intent": "TASK", "confidence": 0.85, "needs_web": false, "reason": "code change"}'
        r = _parse_llm_response(raw)
        assert r is not None
        assert r.intent == Intent.TASK
        assert r.confidence == 0.85
        assert r.source == "llm"

    def test_parse_query_with_web(self):
        raw = '{"intent": "QUERY", "confidence": 0.9, "needs_web": true, "reason": "factual"}'
        r = _parse_llm_response(raw)
        assert r is not None
        assert r.intent == Intent.QUERY
        assert r.needs_web is True

    def test_parse_with_surrounding_prose(self):
        raw = "Sure! Here is the answer: {\"intent\": \"CONVERSATION\", \"confidence\": 0.7}"
        r = _parse_llm_response(raw)
        assert r is not None
        assert r.intent == Intent.CONVERSATION

    def test_parse_invalid_intent(self):
        raw = '{"intent": "SOMETHING_ELSE", "confidence": 0.9}'
        assert _parse_llm_response(raw) is None

    def test_parse_missing_json(self):
        assert _parse_llm_response("Sure thing!") is None

    def test_parse_empty(self):
        assert _parse_llm_response("") is None

    def test_parse_clamps_confidence(self):
        raw = '{"intent": "TASK", "confidence": 5.0}'
        r = _parse_llm_response(raw)
        assert r is not None
        assert r.confidence == 1.0


# ── Recent context serialization ────────────────────────────────────

class TestRecentContext:
    def test_empty_session(self):
        assert _recent_context(None) == ""
        assert _recent_context(SessionContext()) == ""

    def test_serializes_recent_turns(self):
        s = SessionContext()
        s.add_user_message("what is Docker")
        s.add_agent_output("assistant", "Docker is a container platform.")
        s.add_user_message("give me more detail")
        result = _recent_context(s, turns=3)
        assert "what is Docker" in result
        assert "assistant:" in result or "agent:" in result


# ── classify_intent (full pipeline) ─────────────────────────────────

class TestClassifyIntentFastPath:
    @pytest.mark.asyncio
    async def test_obvious_task_skips_llm(self):
        called = []
        async def never_call(**_):
            called.append(True)
            return ""
        r = await classify_intent("fix the null check in foo.py", llm_caller=never_call)
        assert r.intent == Intent.TASK
        assert r.source == "fast"
        assert called == []

    @pytest.mark.asyncio
    async def test_obvious_query_skips_llm(self):
        called = []
        async def never_call(**_):
            called.append(True)
            return ""
        r = await classify_intent("what is the latest airflow version", llm_caller=never_call)
        assert r.intent == Intent.QUERY
        assert r.source == "fast"
        assert r.needs_web is True
        assert called == []

    @pytest.mark.asyncio
    async def test_obvious_conversation_skips_llm(self):
        called = []
        async def never_call(**_):
            called.append(True)
            return ""
        r = await classify_intent("thanks", llm_caller=never_call)
        assert r.intent == Intent.CONVERSATION
        assert r.source == "fast"
        assert called == []

    @pytest.mark.asyncio
    async def test_task_populates_classification(self):
        async def never_call(**_):
            return ""
        r = await classify_intent("fix foo.py", llm_caller=never_call)
        assert r.task_classification is not None


class TestClassifyIntentSlowPath:
    @pytest.mark.asyncio
    async def test_ambiguous_falls_to_llm(self):
        calls = []
        async def llm(**kwargs):
            calls.append(kwargs)
            return '{"intent": "QUERY", "confidence": 0.85, "needs_web": false, "reason": "informational"}'
        r = await classify_intent(
            "that's an interesting thing to note here",
            llm_caller=llm,
        )
        assert len(calls) == 1
        assert r.source == "llm"
        assert r.intent == Intent.QUERY

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_gracefully(self):
        async def broken_llm(**_):
            raise RuntimeError("LLM down")
        r = await classify_intent("some ambiguous text here that isn't obvious", llm_caller=broken_llm)
        assert r.source == "fallback"
        assert r.intent in (Intent.QUERY, Intent.TASK)

    @pytest.mark.asyncio
    async def test_llm_garbage_falls_back(self):
        async def garbage_llm(**_):
            return "I think maybe it is a task?"
        r = await classify_intent("hmm that's weird I wonder what that means", llm_caller=garbage_llm)
        assert r.source == "fallback"

    @pytest.mark.asyncio
    async def test_non_question_fallback_routes_to_task(self):
        async def broken_llm(**_):
            raise RuntimeError("down")
        # Fully ambiguous, non-question, non-imperative — fast path can't decide.
        r = await classify_intent(
            "that thing in the middle over there somewhere on the side",
            llm_caller=broken_llm,
        )
        assert r.source == "fallback"
        assert r.intent == Intent.TASK
        assert r.task_classification is not None


class TestClassifyIntentWithSession:
    @pytest.mark.asyncio
    async def test_session_history_passed_to_llm(self):
        session = SessionContext()
        session.add_user_message("what is Docker")
        session.add_agent_output("assistant", "Docker is a container platform.")

        captured = {}
        async def llm(**kwargs):
            captured.update(kwargs)
            return '{"intent": "CONVERSATION", "confidence": 0.85, "reason": "continuation"}'

        await classify_intent(
            "uhh ok so whats with that",
            session=session,
            llm_caller=llm,
        )
        user_msg = captured["messages"][0]["content"]
        assert "what is Docker" in user_msg


# ── Sanity: IntentClassification dict ──────────────────────────────

class TestIntentClassification:
    def test_to_dict_shape(self):
        r = IntentClassification(intent=Intent.TASK, confidence=0.88, reason="verb")
        d = r.to_dict()
        assert d["intent"] == "task"
        assert d["confidence"] == 0.88
