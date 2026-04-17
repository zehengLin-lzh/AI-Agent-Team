"""Predictive rate-limit tracking per provider.

We keep a rolling 60-second window of (timestamp, tokens) entries and
throttle pre-emptively when usage crosses a soft threshold (default 80%)
of the provider's known limit. Limits for the subset of providers covered
by C2 (Anthropic, OpenAI, OpenRouter) are the safe defaults for typical
tier-1 accounts; they can be overridden via env (e.g.
``ANTHROPIC_RATE_LIMIT=50,40000`` → 50 rpm, 40k tpm).

If no limit is known for a provider, ``should_throttle`` is a no-op.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass
from threading import RLock
from time import time

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0
SOFT_THRESHOLD = 0.80  # start backing off at 80% of limit


@dataclass
class RateLimit:
    rpm: int
    tpm: int


DEFAULT_LIMITS: dict[str, RateLimit] = {
    # Conservative tier-1 defaults — override via env for higher tiers.
    "anthropic": RateLimit(rpm=50, tpm=40_000),
    "openai": RateLimit(rpm=500, tpm=150_000),
    "openrouter": RateLimit(rpm=200, tpm=60_000),
}


def _limit_from_env(provider: str) -> RateLimit | None:
    raw = os.environ.get(f"{provider.upper()}_RATE_LIMIT")
    if not raw:
        return None
    try:
        rpm_s, tpm_s = raw.split(",", 1)
        return RateLimit(rpm=int(rpm_s.strip()), tpm=int(tpm_s.strip()))
    except (ValueError, AttributeError):
        logger.warning("Invalid %s_RATE_LIMIT (expected RPM,TPM): %r", provider.upper(), raw)
        return None


class RateTracker:
    def __init__(self, provider: str, limit: RateLimit | None = None):
        self.provider = provider
        self.limit = limit or _limit_from_env(provider) or DEFAULT_LIMITS.get(provider)
        self.window: deque[tuple[float, int]] = deque()
        self._lock = RLock()

    def _prune(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        while self.window and self.window[0][0] < cutoff:
            self.window.popleft()

    def record(self, tokens: int) -> None:
        with self._lock:
            now = time()
            self._prune(now)
            self.window.append((now, max(0, int(tokens))))

    def should_throttle(self) -> tuple[bool, float]:
        """Return (need_wait, seconds_to_sleep).

        Throttles when either RPM or TPM crosses the soft threshold. The
        wait is the time until the oldest in-window entry drops out.
        """
        if self.limit is None:
            return False, 0.0
        with self._lock:
            now = time()
            self._prune(now)
            if not self.window:
                return False, 0.0
            requests = len(self.window)
            tokens = sum(t for _, t in self.window)
            rpm_limit = max(1, int(self.limit.rpm * SOFT_THRESHOLD))
            tpm_limit = max(1, int(self.limit.tpm * SOFT_THRESHOLD))
            if requests < rpm_limit and tokens < tpm_limit:
                return False, 0.0
            oldest = self.window[0][0]
            wait = max(0.0, (oldest + WINDOW_SECONDS) - now)
            return True, wait

    def snapshot(self) -> dict:
        with self._lock:
            self._prune(time())
            return {
                "provider": self.provider,
                "requests_in_window": len(self.window),
                "tokens_in_window": sum(t for _, t in self.window),
                "rpm_limit": self.limit.rpm if self.limit else None,
                "tpm_limit": self.limit.tpm if self.limit else None,
            }

    def reset(self) -> None:
        with self._lock:
            self.window.clear()


_TRACKERS: dict[str, RateTracker] = {}
_TRACKERS_LOCK = RLock()


def get_tracker(provider: str) -> RateTracker:
    with _TRACKERS_LOCK:
        tracker = _TRACKERS.get(provider)
        if tracker is None:
            tracker = RateTracker(provider)
            _TRACKERS[provider] = tracker
    return tracker


def reset_trackers() -> None:
    with _TRACKERS_LOCK:
        _TRACKERS.clear()
