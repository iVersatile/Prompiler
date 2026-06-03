"""Per-call observability hook — latency, token counts, cost estimate.

Adapters call ``emit_call_metrics`` after a successful ``extract``. The hook,
when supplied, receives a frozen ``BackendCallMetrics`` snapshot. Cost is
computed via a versioned ``PricingTable``; unknown (backend, model) pairs
cost ``0.0`` so a missing pricing entry never blocks observability.

The hook is a ``Protocol``; any object with ``async def on_call(metrics)``
satisfies it — no inheritance required.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BackendCallMetrics:
    backend: str
    model: str
    latency_seconds: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


@runtime_checkable
class ObservabilityHook(Protocol):
    async def on_call(self, metrics: BackendCallMetrics) -> None: ...


@dataclass(frozen=True)
class PricingEntry:
    prompt_per_1m: float
    completion_per_1m: float


@dataclass(frozen=True)
class PricingTable:
    rates: Mapping[tuple[str, str], PricingEntry] = field(default_factory=dict)

    def cost_usd(
        self,
        *,
        backend: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        entry = self.rates.get((backend, model))
        if entry is None:
            return 0.0
        return (
            prompt_tokens * entry.prompt_per_1m / 1_000_000
            + completion_tokens * entry.completion_per_1m / 1_000_000
        )


DEFAULT_PRICING_TABLE = PricingTable(
    rates={
        ("claude", "claude-haiku-4-5-20251001"): PricingEntry(1.0, 5.0),
        ("openai", "gpt-4o-mini"): PricingEntry(0.15, 0.60),
        ("gemini", "gemini-2.5-flash"): PricingEntry(0.30, 2.50),
        ("ollama", "llama3.1"): PricingEntry(0.0, 0.0),
    }
)


async def emit_call_metrics(
    *,
    hook: ObservabilityHook | None,
    backend: str,
    model: str,
    latency_seconds: float,
    prompt_tokens: int,
    completion_tokens: int,
    pricing: PricingTable,
) -> None:
    if hook is None:
        return
    cost = pricing.cost_usd(
        backend=backend,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    await hook.on_call(
        BackendCallMetrics(
            backend=backend,
            model=model,
            latency_seconds=latency_seconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
        )
    )
