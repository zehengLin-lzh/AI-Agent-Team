"""Multi-key rotation per provider.

Discovery convention: in addition to the canonical env var (e.g.
``ANTHROPIC_API_KEY``), we scan for numbered suffixes
``ANTHROPIC_API_KEY_2``, ``ANTHROPIC_API_KEY_3`` (and also bare ``_1`` for
symmetry). Each pool exposes ``get_key()`` which round-robins across
healthy keys and skips any that have been flagged as unusable within the
backoff window.

This module is deliberately additive: if only one key is configured the
pool has exactly one entry and behaves identically to ``keys.get_key``.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from threading import RLock
from time import time

from agent_team.llm.keys import PROVIDER_KEY_NAMES, load_keys_into_env

logger = logging.getLogger(__name__)

DEFAULT_BACKOFF_SECONDS = 300  # 5 min — long enough for short quotas to reset
MAX_FAILS_BEFORE_COOLDOWN = 3


@dataclass
class KeyStatus:
    key: str
    bad_until: float = 0.0
    fail_count: int = 0


@dataclass
class CredentialPool:
    provider: str
    keys: list[KeyStatus] = field(default_factory=list)
    _cursor: int = 0
    _lock: RLock = field(default_factory=RLock, repr=False)

    def refresh(self) -> None:
        """Re-scan the environment and synchronize the pool contents."""
        load_keys_into_env()
        discovered = _discover_keys(self.provider)
        with self._lock:
            existing = {ks.key: ks for ks in self.keys}
            self.keys = [existing.get(k, KeyStatus(key=k)) for k in discovered]
            if self._cursor >= len(self.keys):
                self._cursor = 0

    def size(self) -> int:
        return len(self.keys)

    def healthy_count(self) -> int:
        now = time()
        return sum(1 for k in self.keys if k.bad_until <= now)

    def get_key(self) -> str | None:
        """Return the next healthy key; None if no healthy keys available."""
        with self._lock:
            if not self.keys:
                self.refresh()
            if not self.keys:
                return None
            now = time()
            n = len(self.keys)
            for _ in range(n):
                status = self.keys[self._cursor]
                self._cursor = (self._cursor + 1) % n
                if status.bad_until <= now:
                    return status.key
            # All keys are in cooldown — return the one with the soonest expiry
            soonest = min(self.keys, key=lambda s: s.bad_until)
            return soonest.key

    def flag_bad(
        self,
        key: str,
        reason: str,
        *,
        backoff_seconds: int = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        with self._lock:
            for status in self.keys:
                if status.key == key:
                    status.fail_count += 1
                    if status.fail_count >= MAX_FAILS_BEFORE_COOLDOWN:
                        status.bad_until = time() + backoff_seconds
                        logger.info(
                            "credential_pool: cooling %s key for %ss (reason=%s)",
                            self.provider, backoff_seconds, reason,
                        )
                    return

    def mark_good(self, key: str) -> None:
        with self._lock:
            for status in self.keys:
                if status.key == key:
                    status.bad_until = 0.0
                    status.fail_count = 0
                    return

    def reset(self) -> None:
        with self._lock:
            for status in self.keys:
                status.bad_until = 0.0
                status.fail_count = 0


# ── Module-level pool registry ────────────────────────────────────────

_POOLS: dict[str, CredentialPool] = {}
_POOLS_LOCK = RLock()


def _discover_keys(provider: str) -> list[str]:
    """Scan the environment for all configured keys for ``provider``."""
    base = PROVIDER_KEY_NAMES.get(provider)
    if not base:
        return []
    keys: list[str] = []
    primary = os.environ.get(base, "").strip()
    if primary:
        keys.append(primary)
    pattern = re.compile(rf"^{re.escape(base)}_(\d+)$")
    extras: list[tuple[int, str]] = []
    for name, value in os.environ.items():
        m = pattern.match(name)
        if not m:
            continue
        value = (value or "").strip()
        if not value or value in keys:
            continue
        extras.append((int(m.group(1)), value))
    for _, val in sorted(extras):
        keys.append(val)
    return keys


def get_pool(provider: str) -> CredentialPool:
    """Return (and lazily construct) the credential pool for ``provider``."""
    with _POOLS_LOCK:
        pool = _POOLS.get(provider)
        if pool is None:
            pool = CredentialPool(provider=provider)
            pool.refresh()
            _POOLS[provider] = pool
    return pool


def reset_pools() -> None:
    """Drop all pools (primarily for tests)."""
    with _POOLS_LOCK:
        _POOLS.clear()
