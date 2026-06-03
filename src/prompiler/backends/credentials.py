"""Credential providers — pluggable auth header source for backend adapters.

Two providers ship today:

- ``EnvVarProvider`` reads vendor-specific env vars
  (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``GOOGLE_API_KEY``) and emits
  the matching auth header for each backend.
- ``GoogleADCProvider`` uses Google's Application Default Credentials chain
  (``google.auth.default()``) to mint a bearer token for the Gemini adapter.
  Other backends are rejected.

Missing or invalid credentials raise :class:`CredentialError` — a single-line,
actionable message pointing at ``docs/MANUAL_TESTING.md §3``. The error is
raised at adapter *construction* time, before any HTTP traffic, so callers
get fail-fast feedback (PRD FR-10).

The :class:`Credential` return type carries an opaque ``headers`` dict so
adapters stay oblivious to the *kind* of auth (vendor header vs bearer).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

DOCS_REF = "docs/MANUAL_TESTING.md §3 (credentials)"

_ENV_VAR_BY_BACKEND: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}


class CredentialError(RuntimeError):
    """Raised when a credential cannot be resolved.

    Message is single-line and actionable: names the missing env var (or the
    misconfiguration) and points at ``docs/MANUAL_TESTING.md §3``. No stack
    trace surfaces past the CLI boundary (see ``prompiler.cli``).
    """


@dataclass(frozen=True)
class Credential:
    """Resolved credential — an opaque bundle of HTTP headers.

    Adapters merge ``headers`` into their ``httpx.AsyncClient`` so they never
    need to know whether the auth is an API key or a bearer token.
    """

    headers: dict[str, str]


@runtime_checkable
class CredentialProvider(Protocol):
    """Resolve credentials for a named backend (``claude`` / ``openai`` / ``gemini``)."""

    def resolve(self, backend: str) -> Credential: ...


def _api_key_headers(backend: str, value: str) -> dict[str, str]:
    if backend == "claude":
        return {"x-api-key": value}
    if backend == "openai":
        return {"authorization": f"Bearer {value}"}
    if backend == "gemini":
        return {"x-goog-api-key": value}
    raise CredentialError(f"unknown backend {backend!r} for api-key credentials; see {DOCS_REF}")


class EnvVarProvider:
    """Read API keys from vendor-specific env vars."""

    def resolve(self, backend: str) -> Credential:
        env_var = _ENV_VAR_BY_BACKEND.get(backend)
        if env_var is None:
            raise CredentialError(
                f"EnvVarProvider does not support backend {backend!r}; see {DOCS_REF}"
            )
        value = os.environ.get(env_var)
        if not value:
            raise CredentialError(
                f"missing credential for {backend!r}: set {env_var}; see {DOCS_REF}"
            )
        return Credential(headers=_api_key_headers(backend, value))


class GoogleADCProvider:
    """Mint a bearer token via Google Application Default Credentials.

    ``google-auth`` is imported lazily so callers not using ADC don't pay the
    dependency. Only the ``gemini`` backend is supported; other backends raise
    :class:`CredentialError`.
    """

    def resolve(self, backend: str) -> Credential:
        if backend != "gemini":
            raise CredentialError(
                f"GoogleADCProvider only supports 'gemini', got {backend!r}; see {DOCS_REF}"
            )
        try:
            import google.auth  # type: ignore[import-not-found]
            from google.auth.transport.requests import (  # type: ignore[import-not-found]
                Request,
            )
        except ImportError as exc:
            raise CredentialError(
                "GoogleADCProvider requires 'google-auth' extra: "
                f"pip install 'prompiler[adc]'; see {DOCS_REF}"
            ) from exc
        try:
            creds, _project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(Request())
        except Exception as exc:
            raise CredentialError(
                f"Google ADC resolution failed: {exc.__class__.__name__}; see {DOCS_REF}"
            ) from exc
        token = getattr(creds, "token", None)
        if not token:
            raise CredentialError(f"Google ADC returned empty token; see {DOCS_REF}")
        return Credential(headers={"authorization": f"Bearer {token}"})
