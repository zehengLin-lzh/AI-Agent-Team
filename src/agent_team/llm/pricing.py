"""Per-token pricing table and per-session cost aggregation.

Pricing is stored as USD per 1,000,000 input/output tokens to match the way
providers publish their rate cards. The table is a last-known-good snapshot
and should be refreshed periodically; unknown models fall through to a
zero-cost entry so tracking never fails on a new model, it just under-reports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from threading import RLock


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens."""
    input_per_m: float
    output_per_m: float
    cache_read_per_m: float = 0.0
    cache_write_per_m: float = 0.0


# Provider → [(model glob, price)]. Iteration order matters — first match wins.
PRICING: dict[str, list[tuple[str, ModelPrice]]] = {
    "anthropic": [
        ("claude-opus-4*",     ModelPrice(15.00, 75.00, 1.50, 18.75)),
        ("claude-sonnet-4*",   ModelPrice( 3.00, 15.00, 0.30,  3.75)),
        ("claude-haiku-4*",    ModelPrice( 0.80,  4.00, 0.08,  1.00)),
    ],
    "openai": [
        ("gpt-4o-mini*",       ModelPrice( 0.15,  0.60)),
        ("gpt-4o*",            ModelPrice( 2.50, 10.00)),
        ("gpt-4.1-nano*",      ModelPrice( 0.10,  0.40)),
        ("gpt-4.1-mini*",      ModelPrice( 0.40,  1.60)),
        ("gpt-4.1*",           ModelPrice( 2.00,  8.00)),
        ("o1-mini*",           ModelPrice( 3.00, 12.00)),
        ("o1*",                ModelPrice(15.00, 60.00)),
        ("o3-mini*",           ModelPrice( 1.10,  4.40)),
    ],
    # OpenRouter is a proxy; their API returns the real per-request cost in
    # the response. Tracking here is best-effort based on model family.
    "openrouter": [
        ("*:free",             ModelPrice( 0.00,  0.00)),
        ("anthropic/*",        ModelPrice( 3.00, 15.00)),
        ("openai/gpt-4o*",     ModelPrice( 2.50, 10.00)),
    ],
}

ZERO_PRICE = ModelPrice(0.0, 0.0)


def lookup_price(provider: str, model: str) -> ModelPrice:
    for pattern, price in PRICING.get(provider, []):
        if fnmatch(model, pattern):
            return price
    return ZERO_PRICE


@dataclass
class UsageRecord:
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class SessionUsage:
    records: list[UsageRecord] = field(default_factory=list)
    _lock: RLock = field(default_factory=RLock, repr=False)

    def record(
        self,
        provider: str,
        model: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> UsageRecord:
        price = lookup_price(provider, model)
        # Billed input = full prompt minus cached portion, billed separately.
        billed_prompt = max(0, prompt_tokens - cache_read_tokens - cache_write_tokens)
        cost = (
            billed_prompt        * price.input_per_m       / 1_000_000
            + completion_tokens  * price.output_per_m      / 1_000_000
            + cache_read_tokens  * price.cache_read_per_m  / 1_000_000
            + cache_write_tokens * price.cache_write_per_m / 1_000_000
        )
        rec = UsageRecord(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=round(cost, 6),
        )
        with self._lock:
            self.records.append(rec)
        return rec

    def total_cost(self) -> float:
        with self._lock:
            return round(sum(r.cost_usd for r in self.records), 6)

    def summary(self) -> dict:
        with self._lock:
            by_model: dict[tuple[str, str], dict] = {}
            for r in self.records:
                key = (r.provider, r.model)
                agg = by_model.setdefault(
                    key,
                    {
                        "provider": r.provider,
                        "model": r.model,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "cost_usd": 0.0,
                    },
                )
                agg["prompt_tokens"] += r.prompt_tokens
                agg["completion_tokens"] += r.completion_tokens
                agg["cache_read_tokens"] += r.cache_read_tokens
                agg["cache_write_tokens"] += r.cache_write_tokens
                agg["cost_usd"] = round(agg["cost_usd"] + r.cost_usd, 6)
            return {
                "total_cost_usd": self.total_cost(),
                "requests": len(self.records),
                "by_model": list(by_model.values()),
            }


# Module-level session usage — one per process. For embedded/test scenarios,
# callers can construct their own SessionUsage and pass it around.
_current = SessionUsage()
_lock = RLock()


def current_session_usage() -> SessionUsage:
    return _current


def reset_current_session() -> SessionUsage:
    global _current
    with _lock:
        _current = SessionUsage()
    return _current
