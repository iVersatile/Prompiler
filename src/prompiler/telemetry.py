"""Opt-in OpenTelemetry exporter for backend-call metrics (``--telemetry``).

Telemetry is OFF by default (threat S11): nothing leaves the host unless the
operator explicitly enables it. When enabled, each backend call emits one span
whose attributes are an explicit allow-list of usage metrics only — token
counts, latency, model identity, and cost. Prompt and response text are never
recorded, which is structurally guaranteed because :class:`BackendCallMetrics`
carries no text payload in the first place.

Like the pricing loader and usage sink, this module degrades rather than fails:
if the optional ``opentelemetry`` dependency is absent, :func:`build_telemetry_hook`
warns and returns ``None`` instead of raising.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from prompiler.backends.observability import BackendCallMetrics

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

_TRACER_NAME = "prompiler"
_SPAN_NAME = "prompiler.backend_call"

ALLOWED_SPAN_ATTRIBUTES = frozenset(
    {
        "prompiler.backend",
        "prompiler.model",
        "prompiler.latency_seconds",
        "prompiler.prompt_tokens",
        "prompiler.completion_tokens",
        "prompiler.cost_usd",
    }
)


def span_attributes(metrics: BackendCallMetrics) -> dict[str, str | int | float]:
    """Map metrics onto the S11 allow-list — usage scalars only, never text."""
    return {
        "prompiler.backend": metrics.backend,
        "prompiler.model": metrics.model,
        "prompiler.latency_seconds": metrics.latency_seconds,
        "prompiler.prompt_tokens": metrics.prompt_tokens,
        "prompiler.completion_tokens": metrics.completion_tokens,
        "prompiler.cost_usd": metrics.cost_usd,
    }


class OtelHook:
    """:class:`ObservabilityHook` that emits one OTel span per backend call."""

    def __init__(self, *, tracer: Tracer) -> None:
        self._tracer = tracer

    async def on_call(self, metrics: BackendCallMetrics) -> None:
        with self._tracer.start_as_current_span(_SPAN_NAME) as span:
            for key, value in span_attributes(metrics).items():
                span.set_attribute(key, value)


def build_telemetry_hook(*, enabled: bool) -> OtelHook | None:
    """Return an :class:`OtelHook` when enabled, else ``None``.

    Degrade-never: if ``opentelemetry`` is not installed, warn and return
    ``None`` rather than raise — telemetry is an optional capability.
    """
    if not enabled:
        return None
    try:
        from opentelemetry import trace
    except ImportError:
        warnings.warn(
            "--telemetry requested but opentelemetry is not installed; "
            "install the 'telemetry' extra to enable span export. "
            "Continuing without telemetry.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    return OtelHook(tracer=trace.get_tracer(_TRACER_NAME))
