"""Tests for C2 Provider Industrialization.

Covers credential_pool, rate_tracker, prompt_cache, pricing.
"""
from __future__ import annotations

import pytest

from agent_team.llm.credential_pool import (
    CredentialPool,
    _discover_keys,
    get_pool,
    reset_pools,
)
from agent_team.llm.prompt_cache import (
    CACHE_STRATEGY_ANTHROPIC,
    CACHE_STRATEGY_NONE,
    MIN_CACHEABLE_CHARS,
    build_anthropic_system,
    get_cache_strategy,
)
from agent_team.llm.pricing import (
    SessionUsage,
    lookup_price,
)
from agent_team.llm.rate_tracker import (
    DEFAULT_LIMITS,
    RateLimit,
    RateTracker,
    SOFT_THRESHOLD,
    reset_trackers,
)


# ── credential_pool ───────────────────────────────────────────────────

class TestCredentialDiscovery:
    def test_discover_single_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-a")
        monkeypatch.delenv("ANTHROPIC_API_KEY_2", raising=False)
        assert _discover_keys("anthropic") == ["sk-ant-a"]

    def test_discover_multiple_ordered(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "primary")
        monkeypatch.setenv("ANTHROPIC_API_KEY_2", "second")
        monkeypatch.setenv("ANTHROPIC_API_KEY_5", "fifth")
        assert _discover_keys("anthropic") == ["primary", "second", "fifth"]

    def test_discover_unknown_provider(self):
        assert _discover_keys("bogus") == []

    def test_discover_skips_empty_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        monkeypatch.setenv("ANTHROPIC_API_KEY_2", "")
        monkeypatch.setenv("ANTHROPIC_API_KEY_3", "c")
        assert _discover_keys("anthropic") == ["a", "c"]


class TestCredentialPool:
    def setup_method(self):
        reset_pools()

    def test_round_robin(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
        monkeypatch.setenv("ANTHROPIC_API_KEY_2", "k2")
        monkeypatch.setenv("ANTHROPIC_API_KEY_3", "k3")
        pool = CredentialPool("anthropic")
        pool.refresh()
        assert {pool.get_key() for _ in range(6)} == {"k1", "k2", "k3"}

    def test_flag_bad_after_threshold(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "bad")
        monkeypatch.setenv("ANTHROPIC_API_KEY_2", "good")
        pool = CredentialPool("anthropic")
        pool.refresh()
        for _ in range(3):
            pool.flag_bad("bad", "auth error")
        # Now "bad" is cooling; 10 consecutive fetches should all be "good"
        keys = [pool.get_key() for _ in range(10)]
        assert all(k == "good" for k in keys)

    def test_get_pool_is_singleton_per_provider(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        p1 = get_pool("anthropic")
        p2 = get_pool("anthropic")
        assert p1 is p2

    def test_pool_returns_none_when_empty(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY_2", raising=False)
        pool = CredentialPool("anthropic")
        pool.refresh()
        assert pool.get_key() is None


# ── rate_tracker ─────────────────────────────────────────────────────

class TestRateTracker:
    def setup_method(self):
        reset_trackers()

    def test_default_limits_present(self):
        assert "anthropic" in DEFAULT_LIMITS
        assert "openai" in DEFAULT_LIMITS
        assert "openrouter" in DEFAULT_LIMITS

    def test_no_throttle_below_soft_threshold(self):
        tracker = RateTracker("anthropic", RateLimit(rpm=100, tpm=100_000))
        for _ in range(50):
            tracker.record(100)
        need_wait, _ = tracker.should_throttle()
        assert need_wait is False

    def test_throttle_above_rpm_soft_threshold(self):
        tracker = RateTracker("anthropic", RateLimit(rpm=10, tpm=1_000_000))
        soft = int(10 * SOFT_THRESHOLD)
        for _ in range(soft + 1):
            tracker.record(1)
        need_wait, wait_s = tracker.should_throttle()
        assert need_wait is True
        assert wait_s >= 0.0

    def test_throttle_above_tpm_soft_threshold(self):
        tracker = RateTracker("anthropic", RateLimit(rpm=10_000, tpm=100))
        tracker.record(1000)
        need_wait, _ = tracker.should_throttle()
        assert need_wait is True

    def test_no_limit_is_noop(self):
        tracker = RateTracker("zzz-unknown", None)
        tracker.record(10_000_000)
        assert tracker.should_throttle() == (False, 0.0)

    def test_env_override_parses(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_RATE_LIMIT", "20,5000")
        reset_trackers()
        from agent_team.llm.rate_tracker import get_tracker as _get
        tracker = _get("anthropic")
        assert tracker.limit.rpm == 20
        assert tracker.limit.tpm == 5000

    def test_snapshot_reports_counts(self):
        tracker = RateTracker("anthropic", RateLimit(rpm=100, tpm=100_000))
        tracker.record(50)
        tracker.record(150)
        snap = tracker.snapshot()
        assert snap["requests_in_window"] == 2
        assert snap["tokens_in_window"] == 200


# ── prompt_cache ─────────────────────────────────────────────────────

class TestPromptCache:
    def test_strategy_anthropic(self):
        assert get_cache_strategy("anthropic") == CACHE_STRATEGY_ANTHROPIC

    def test_strategy_other(self):
        assert get_cache_strategy("openai") == CACHE_STRATEGY_NONE
        assert get_cache_strategy("openrouter") == CACHE_STRATEGY_NONE

    def test_short_prompt_is_plain_string(self):
        assert build_anthropic_system("short one") == "short one"

    def test_long_prompt_becomes_cache_block(self):
        long = "x" * (MIN_CACHEABLE_CHARS + 10)
        result = build_anthropic_system(long)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[0]["cache_control"] == {"type": "ephemeral"}
        assert result[0]["text"] == long

    def test_empty_passes_through(self):
        assert build_anthropic_system("") == ""


# ── pricing ───────────────────────────────────────────────────────────

class TestPricing:
    def test_sonnet_4_match(self):
        price = lookup_price("anthropic", "claude-sonnet-4-20250514")
        assert price.input_per_m == 3.00
        assert price.output_per_m == 15.00

    def test_opus_4_match(self):
        price = lookup_price("anthropic", "claude-opus-4-20250514")
        assert price.input_per_m == 15.00

    def test_gpt_4o_match(self):
        assert lookup_price("openai", "gpt-4o").input_per_m == 2.50

    def test_gpt_4o_mini_match(self):
        assert lookup_price("openai", "gpt-4o-mini").input_per_m == 0.15

    def test_free_openrouter(self):
        assert lookup_price("openrouter", "qwen/qwen3-coder:free").input_per_m == 0.0

    def test_unknown_is_zero(self):
        assert lookup_price("openai", "future-model-9000").input_per_m == 0.0

    def test_session_aggregate(self):
        session = SessionUsage()
        session.record("anthropic", "claude-sonnet-4-20250514",
                       prompt_tokens=10_000, completion_tokens=2_000)
        session.record("anthropic", "claude-sonnet-4-20250514",
                       prompt_tokens=5_000, completion_tokens=1_000,
                       cache_read_tokens=3_000)
        summary = session.summary()
        assert summary["requests"] == 2
        assert summary["total_cost_usd"] > 0
        assert len(summary["by_model"]) == 1
        model_row = summary["by_model"][0]
        assert model_row["prompt_tokens"] == 15_000
        assert model_row["completion_tokens"] == 3_000

    def test_cache_tokens_reduce_billed_prompt(self):
        session = SessionUsage()
        rec_uncached = session.record(
            "anthropic", "claude-sonnet-4-20250514",
            prompt_tokens=10_000, completion_tokens=0,
        )
        session2 = SessionUsage()
        rec_cached = session2.record(
            "anthropic", "claude-sonnet-4-20250514",
            prompt_tokens=10_000, completion_tokens=0,
            cache_read_tokens=9_000,
        )
        assert rec_cached.cost_usd < rec_uncached.cost_usd


# ── registry wiring smoke test ───────────────────────────────────────

class TestRegistryWiring:
    @pytest.mark.asyncio
    async def test_wait_if_throttled_noop_for_non_industrialized(self):
        from agent_team.llm.registry import _wait_if_throttled
        # Should simply return without sleeping
        await _wait_if_throttled("ollama")

    @pytest.mark.asyncio
    async def test_wait_if_throttled_runs_for_anthropic(self, monkeypatch):
        from agent_team.llm.registry import _wait_if_throttled
        # Not actually rate-limited; the call should return immediately
        reset_trackers()
        await _wait_if_throttled("anthropic")
