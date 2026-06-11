"""Credential providers — pluggable auth header source for backend adapters.

Four providers ship today:

- ``EnvVarProvider`` reads vendor-specific env vars
  (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``GOOGLE_API_KEY``) and emits
  the matching auth header for each backend.
- ``GoogleADCProvider`` uses Google's Application Default Credentials chain
  (``google.auth.default()``) to mint a bearer token for the Gemini adapter.
  Other backends are rejected.
- ``KeychainProvider`` reads API keys from the OS keychain via ``keyring``.
- ``OAuthProvider`` returns a cached/refreshed bearer token from a file-backed
  token store. The interactive grant is primed by the ``prompiler login``
  command; ``resolve`` itself is headless — it serves a cached token, performs
  a ``refresh_token`` grant when the cached token is near expiry, and raises a
  "run ``prompiler login``" error when the store is unprimed.

Missing or invalid credentials raise :class:`CredentialError` — a single-line,
actionable message pointing at ``docs/MANUAL_TESTING.md §3``. The error is
raised at adapter *construction* time, before any HTTP traffic, so callers
get fail-fast feedback (PRD FR-10).

The :class:`Credential` return type carries an opaque ``headers`` dict so
adapters stay oblivious to the *kind* of auth (vendor header vs bearer).
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

DOCS_REF = "docs/MANUAL_TESTING.md §3 (credentials)"

_ENV_VAR_BY_BACKEND: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}

_KEYCHAIN_SERVICE = "prompiler"

_OAUTH_STORE_ENV = "PROMPILER_OAUTH_STORE"
_OAUTH_EXPIRY_LEEWAY_SECONDS = 60


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


class KeychainProvider:
    """Read API keys from the OS keychain via the ``keyring`` library.

    ``keyring`` is imported lazily so callers not using the keychain don't pay
    the dependency. Secrets are stored under the ``prompiler`` service, keyed by
    backend name; a missing entry raises :class:`CredentialError`.
    """

    def resolve(self, backend: str) -> Credential:
        if backend not in _ENV_VAR_BY_BACKEND:
            raise CredentialError(
                f"KeychainProvider does not support backend {backend!r}; see {DOCS_REF}"
            )
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CredentialError(
                "KeychainProvider requires 'keyring' extra: "
                f"pip install 'prompiler[keychain]'; see {DOCS_REF}"
            ) from exc
        value = keyring.get_password(_KEYCHAIN_SERVICE, backend)
        if not value:
            raise CredentialError(
                f"missing keychain credential for {backend!r}: "
                f"add it under service {_KEYCHAIN_SERVICE!r}; see {DOCS_REF}"
            )
        return Credential(headers=_api_key_headers(backend, value))


def _default_token_store_path() -> Path:
    override = os.environ.get(_OAUTH_STORE_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "prompiler" / "oauth_tokens.json"


def _read_token_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialError(
            f"OAuth token store at {path} is unreadable: {exc.__class__.__name__}; see {DOCS_REF}"
        ) from exc
    if not isinstance(data, dict):
        raise CredentialError(
            f"OAuth token store at {path} is malformed (expected object); see {DOCS_REF}"
        )
    return data


def _write_token_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store), encoding="utf-8")
    path.chmod(0o600)


class OAuthProvider:
    """Serve a cached/refreshed bearer token from a file-backed token store.

    The interactive grant is primed out-of-band by ``prompiler login``; this
    provider's ``resolve`` is headless. It returns a cached token, performs a
    ``refresh_token`` grant when the cached token is within
    ``_OAUTH_EXPIRY_LEEWAY_SECONDS`` of expiry, and raises a "run
    ``prompiler login``" error when the store is unprimed for the backend.

    ``store_path``/``client``/``now`` are injectable for tests; defaults read
    from ``PROMPILER_OAUTH_STORE`` (or ``~/.config/prompiler``), a fresh
    ``httpx.Client``, and the wall clock.
    """

    def __init__(
        self,
        store_path: Path | None = None,
        client: httpx.Client | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._store_path = store_path if store_path is not None else _default_token_store_path()
        self._client = client if client is not None else httpx.Client()
        self._now = now if now is not None else time.time

    def resolve(self, backend: str) -> Credential:
        store = _read_token_store(self._store_path)
        entry = store.get(backend)
        if not isinstance(entry, dict):
            raise CredentialError(
                f"no OAuth token for {backend!r}: run `prompiler login {backend}`; see {DOCS_REF}"
            )
        access_token = entry.get("access_token")
        expires_at = float(entry.get("expires_at", 0.0))
        now = self._now()
        if access_token and now < expires_at - _OAUTH_EXPIRY_LEEWAY_SECONDS:
            return Credential(headers={"authorization": f"Bearer {access_token}"})
        return self._refresh(backend, entry, store, now)

    def _refresh(
        self,
        backend: str,
        entry: dict[str, Any],
        store: dict[str, Any],
        now: float,
    ) -> Credential:
        token_url = entry.get("token_url")
        refresh_token = entry.get("refresh_token")
        client_id = entry.get("client_id")
        if not (token_url and refresh_token and client_id):
            raise CredentialError(
                f"OAuth token for {backend!r} cannot refresh "
                f"(missing token_url/refresh_token/client_id): "
                f"run `prompiler login {backend}`; see {DOCS_REF}"
            )
        data: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        client_secret = entry.get("client_secret")
        if client_secret:
            data["client_secret"] = client_secret
        try:
            response = self._client.post(token_url, data=data)
        except httpx.HTTPError as exc:
            raise CredentialError(
                f"OAuth refresh for {backend!r} failed: {exc.__class__.__name__}; see {DOCS_REF}"
            ) from exc
        if response.status_code // 100 != 2:
            raise CredentialError(
                f"OAuth refresh for {backend!r} failed: HTTP {response.status_code}; see {DOCS_REF}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CredentialError(
                f"OAuth refresh for {backend!r} returned a non-JSON body; see {DOCS_REF}"
            ) from exc
        new_token = payload.get("access_token")
        if not new_token:
            raise CredentialError(
                f"OAuth refresh for {backend!r} returned no access_token; see {DOCS_REF}"
            )
        try:
            expires_in = float(payload.get("expires_in", 3600))
        except (TypeError, ValueError):
            raise CredentialError(
                f"OAuth refresh for {backend!r} returned a non-numeric expires_in; see {DOCS_REF}"
            ) from None
        if not math.isfinite(expires_in) or expires_in <= 0:
            raise CredentialError(
                f"OAuth refresh for {backend!r} returned an out-of-range expires_in; see {DOCS_REF}"
            )
        updated = dict(entry)
        updated["access_token"] = new_token
        updated["expires_at"] = now + expires_in
        store[backend] = updated
        _write_token_store(self._store_path, store)
        return Credential(headers={"authorization": f"Bearer {new_token}"})
