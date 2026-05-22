"""Backend adapters — vendor-specific extract() implementations.

Public surface re-exports the ``BackendAdapter`` Protocol so callers do
``from prompiler.backends import BackendAdapter`` without depending on the
``base`` module path. Concrete adapters (mock today; claude / openai /
gemini / ollama in later P2.x tasks) live in sibling modules.
"""

from __future__ import annotations

from prompiler.backends.base import BackendAdapter
from prompiler.backends.claude import ClaudeAdapter
from prompiler.backends.credentials import (
    Credential,
    CredentialError,
    CredentialProvider,
    EnvVarProvider,
    GoogleADCProvider,
)
from prompiler.backends.gemini import GeminiAdapter
from prompiler.backends.ollama import OllamaAdapter
from prompiler.backends.openai import OpenAIAdapter

__all__ = [
    "BackendAdapter",
    "ClaudeAdapter",
    "Credential",
    "CredentialError",
    "CredentialProvider",
    "EnvVarProvider",
    "GeminiAdapter",
    "GoogleADCProvider",
    "OllamaAdapter",
    "OpenAIAdapter",
]
