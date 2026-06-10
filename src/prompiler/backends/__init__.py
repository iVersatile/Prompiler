"""Backend adapters — vendor-specific extract() implementations.

Public surface re-exports the ``BackendAdapter`` Protocol so callers do
``from prompiler.backends import BackendAdapter`` without depending on the
``base`` module path. Concrete adapters (mock today; claude / openai /
gemini / ollama in later P2.x tasks) live in sibling modules.
"""

from __future__ import annotations

from prompiler.backends.base import (
    BackendAdapter,
    CapabilityError,
    ExtractResult,
    ModalContent,
)
from prompiler.backends.claude import ClaudeAdapter
from prompiler.backends.credentials import (
    Credential,
    CredentialError,
    CredentialProvider,
    EnvVarProvider,
    GoogleADCProvider,
)
from prompiler.backends.gemini import GeminiAdapter
from prompiler.backends.observability import (
    DEFAULT_PRICING_TABLE,
    BackendCallMetrics,
    ObservabilityHook,
    PricingEntry,
    PricingTable,
    emit_call_metrics,
)
from prompiler.backends.ollama import OllamaAdapter
from prompiler.backends.openai import OpenAIAdapter
from prompiler.backends.retry import RetryPolicy, with_retry

__all__ = [
    "DEFAULT_PRICING_TABLE",
    "BackendAdapter",
    "BackendCallMetrics",
    "CapabilityError",
    "ClaudeAdapter",
    "Credential",
    "CredentialError",
    "CredentialProvider",
    "EnvVarProvider",
    "ExtractResult",
    "GeminiAdapter",
    "GoogleADCProvider",
    "ModalContent",
    "ObservabilityHook",
    "OllamaAdapter",
    "OpenAIAdapter",
    "PricingEntry",
    "PricingTable",
    "RetryPolicy",
    "emit_call_metrics",
    "with_retry",
]
