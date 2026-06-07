"""Tests for the opt-in OpenTelemetry exporter (``--telemetry``).

Telemetry is OFF by default (threat S11): no spans are produced and nothing
leaves the host unless explicitly enabled. When enabled, span attributes are an
explicit allow-list of usage metrics only — never prompt or response text.
"""

from __future__ import annotations

import asyncio

import pytest

from prompiler.backends.observability import BackendCallMetrics
from prompiler.telemetry import (
    ALLOWED_SPAN_ATTRIBUTES,
    OtelHook,
    build_telemetry_hook,
    span_attributes,
)

_METRICS = BackendCallMetrics(
    backend="ollama",
    model="llama3.1",
    latency_seconds=1.25,
    prompt_tokens=120,
    completion_tokens=34,
    cost_usd=0.0007,
)


@pytest.mark.unit
def test_disabled_by_default_returns_none() -> None:
    assert build_telemetry_hook(enabled=False) is None


@pytest.mark.unit
def test_enabled_returns_otel_hook() -> None:
    hook = build_telemetry_hook(enabled=True)
    assert isinstance(hook, OtelHook)


@pytest.mark.unit
def test_allow_list_is_exactly_the_metric_fields() -> None:
    # S11: only usage counts / IDs / cost — never input or output text.
    assert (
        frozenset(
            {
                "prompiler.backend",
                "prompiler.model",
                "prompiler.latency_seconds",
                "prompiler.prompt_tokens",
                "prompiler.completion_tokens",
                "prompiler.cost_usd",
            }
        )
        == ALLOWED_SPAN_ATTRIBUTES
    )


@pytest.mark.unit
def test_span_attributes_only_emit_allow_listed_keys() -> None:
    attrs = span_attributes(_METRICS)
    assert set(attrs) == set(ALLOWED_SPAN_ATTRIBUTES)


@pytest.mark.unit
def test_span_attributes_carry_no_text_payload() -> None:
    attrs = span_attributes(_METRICS)
    assert attrs["prompiler.backend"] == "ollama"
    assert attrs["prompiler.model"] == "llama3.1"
    assert attrs["prompiler.prompt_tokens"] == 120
    assert attrs["prompiler.completion_tokens"] == 34
    assert attrs["prompiler.cost_usd"] == pytest.approx(0.0007)


@pytest.mark.unit
def test_otel_hook_emits_one_span_per_call() -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    hook = OtelHook(tracer=tracer)
    asyncio.run(hook.on_call(_METRICS))

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert set(span.attributes) == set(ALLOWED_SPAN_ATTRIBUTES)
    assert span.attributes["prompiler.prompt_tokens"] == 120
