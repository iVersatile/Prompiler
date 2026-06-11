"""Unit tests for ``prompiler.backends.credentials`` (P2 credentials task).

Covers:

- ``EnvVarProvider`` happy/error paths per backend.
- ``GoogleADCProvider`` rejection of non-gemini backends, missing
  ``google-auth`` dependency, happy path with mocked ``google.auth.default``,
  empty-token path, and refresh failure path.
- :class:`CredentialError` message hygiene: single-line, points at
  ``docs/MANUAL_TESTING.md §3``.
- Adapter wiring priority for Claude / OpenAI / Gemini:
  ``client > api_key > credentials > raise CredentialError``.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest

from prompiler.backends import (
    ClaudeAdapter,
    Credential,
    CredentialError,
    EnvVarProvider,
    GeminiAdapter,
    GoogleADCProvider,
    KeychainProvider,
    OAuthProvider,
    OpenAIAdapter,
)
from prompiler.backends.credentials import DOCS_REF

ALL_ENV_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def _clear_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no real credentials leak into tests."""
    for name in ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# EnvVarProvider --------------------------------------------------------
@pytest.mark.unit
def test_envvar_provider_claude_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-1")
    cred = EnvVarProvider().resolve("claude")
    assert cred == Credential(headers={"x-api-key": "sk-ant-1"})


@pytest.mark.unit
def test_envvar_provider_openai_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-1")
    cred = EnvVarProvider().resolve("openai")
    assert cred == Credential(headers={"authorization": "Bearer sk-oai-1"})


@pytest.mark.unit
def test_envvar_provider_gemini_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "gg-1")
    cred = EnvVarProvider().resolve("gemini")
    assert cred == Credential(headers={"x-goog-api-key": "gg-1"})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("backend", "env_var"),
    [
        ("claude", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("gemini", "GOOGLE_API_KEY"),
    ],
)
def test_envvar_provider_missing_env_var(backend: str, env_var: str) -> None:
    with pytest.raises(CredentialError) as excinfo:
        EnvVarProvider().resolve(backend)
    message = str(excinfo.value)
    assert env_var in message
    assert DOCS_REF in message
    assert backend in message


@pytest.mark.unit
def test_envvar_provider_unknown_backend() -> None:
    with pytest.raises(CredentialError) as excinfo:
        EnvVarProvider().resolve("ollama")
    message = str(excinfo.value)
    assert "ollama" in message
    assert DOCS_REF in message


@pytest.mark.unit
def test_envvar_provider_error_is_single_line() -> None:
    with pytest.raises(CredentialError) as excinfo:
        EnvVarProvider().resolve("claude")
    assert "\n" not in str(excinfo.value)


# GoogleADCProvider -----------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("backend", ["claude", "openai", "ollama"])
def test_google_adc_rejects_non_gemini(backend: str) -> None:
    with pytest.raises(CredentialError) as excinfo:
        GoogleADCProvider().resolve(backend)
    message = str(excinfo.value)
    assert "gemini" in message
    assert backend in message
    assert DOCS_REF in message
    assert "\n" not in message


@pytest.mark.unit
def test_google_adc_missing_google_auth_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "google.auth", None)
    with pytest.raises(CredentialError) as excinfo:
        GoogleADCProvider().resolve("gemini")
    message = str(excinfo.value)
    assert "google-auth" in message
    assert "pip install" in message
    assert "prompiler[adc]" in message
    assert DOCS_REF in message
    assert "\n" not in message


def _install_fake_google_auth(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str | None,
    raise_on_refresh: Exception | None = None,
) -> dict[str, Any]:
    recorder: dict[str, Any] = {"default_kwargs": None, "refresh_called": False}

    class _FakeCreds:
        def __init__(self) -> None:
            self.token: str | None = None

        def refresh(self, request: object) -> None:
            recorder["refresh_called"] = True
            if raise_on_refresh is not None:
                raise raise_on_refresh
            self.token = token

    class _FakeRequest:
        pass

    def _fake_default(*, scopes: list[str]) -> tuple[_FakeCreds, str]:
        recorder["default_kwargs"] = {"scopes": scopes}
        return _FakeCreds(), "fake-project"

    google_module = types.ModuleType("google")
    google_auth_module = types.ModuleType("google.auth")
    google_auth_module.default = _fake_default  # type: ignore[attr-defined]
    transport_module = types.ModuleType("google.auth.transport")
    requests_module = types.ModuleType("google.auth.transport.requests")
    requests_module.Request = _FakeRequest  # type: ignore[attr-defined]
    transport_module.requests = requests_module  # type: ignore[attr-defined]
    google_auth_module.transport = transport_module  # type: ignore[attr-defined]
    google_module.auth = google_auth_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.auth", google_auth_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport", transport_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", requests_module)
    return recorder


@pytest.mark.unit
def test_google_adc_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install_fake_google_auth(monkeypatch, token="ya29.fake")
    cred = GoogleADCProvider().resolve("gemini")
    assert cred == Credential(headers={"authorization": "Bearer ya29.fake"})
    assert recorder["refresh_called"] is True
    assert recorder["default_kwargs"] == {
        "scopes": ["https://www.googleapis.com/auth/cloud-platform"]
    }


@pytest.mark.unit
def test_google_adc_empty_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_google_auth(monkeypatch, token=None)
    with pytest.raises(CredentialError) as excinfo:
        GoogleADCProvider().resolve("gemini")
    message = str(excinfo.value)
    assert "empty token" in message
    assert DOCS_REF in message
    assert "\n" not in message


@pytest.mark.unit
def test_google_adc_refresh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_google_auth(
        monkeypatch,
        token="unused",
        raise_on_refresh=RuntimeError("network down"),
    )
    with pytest.raises(CredentialError) as excinfo:
        GoogleADCProvider().resolve("gemini")
    message = str(excinfo.value)
    assert "RuntimeError" in message
    assert DOCS_REF in message
    assert "\n" not in message


# KeychainProvider -----------------------------------------------------
def _install_fake_keyring(
    monkeypatch: pytest.MonkeyPatch,
    *,
    password: str | None,
) -> dict[str, Any]:
    recorder: dict[str, Any] = {"get_password_args": None}

    def _fake_get_password(service: str, username: str) -> str | None:
        recorder["get_password_args"] = {"service": service, "username": username}
        return password

    keyring_module = types.ModuleType("keyring")
    keyring_module.get_password = _fake_get_password  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", keyring_module)
    return recorder


@pytest.mark.unit
@pytest.mark.parametrize(
    ("backend", "expected_headers"),
    [
        ("claude", {"x-api-key": "kc-claude"}),
        ("openai", {"authorization": "Bearer kc-openai"}),
        ("gemini", {"x-goog-api-key": "kc-gemini"}),
    ],
)
def test_keychain_provider_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    expected_headers: dict[str, str],
) -> None:
    recorder = _install_fake_keyring(monkeypatch, password=f"kc-{backend}")
    cred = KeychainProvider().resolve(backend)
    assert cred == Credential(headers=expected_headers)
    assert recorder["get_password_args"] == {"service": "prompiler", "username": backend}


@pytest.mark.unit
def test_keychain_provider_unknown_backend() -> None:
    with pytest.raises(CredentialError) as excinfo:
        KeychainProvider().resolve("ollama")
    message = str(excinfo.value)
    assert "ollama" in message
    assert DOCS_REF in message
    assert "\n" not in message


@pytest.mark.unit
def test_keychain_provider_missing_keyring_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    with pytest.raises(CredentialError) as excinfo:
        KeychainProvider().resolve("claude")
    message = str(excinfo.value)
    assert "keyring" in message
    assert "pip install" in message
    assert "prompiler[keychain]" in message
    assert DOCS_REF in message
    assert "\n" not in message


@pytest.mark.unit
def test_keychain_provider_missing_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_keyring(monkeypatch, password=None)
    with pytest.raises(CredentialError) as excinfo:
        KeychainProvider().resolve("claude")
    message = str(excinfo.value)
    assert "claude" in message
    assert DOCS_REF in message
    assert "\n" not in message


# Adapter wiring priority ----------------------------------------------
class _RecordingProvider:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers
        self.calls: list[str] = []

    def resolve(self, backend: str) -> Credential:
        self.calls.append(backend)
        return Credential(headers=self._headers)


class _ExplodingProvider:
    def resolve(self, backend: str) -> Credential:
        raise AssertionError("provider must not be consulted on this path")


@pytest.mark.unit
def test_claude_adapter_no_args_raises() -> None:
    with pytest.raises(CredentialError) as excinfo:
        ClaudeAdapter()
    message = str(excinfo.value)
    assert "ClaudeAdapter" in message
    assert DOCS_REF in message


@pytest.mark.unit
def test_openai_adapter_no_args_raises() -> None:
    with pytest.raises(CredentialError) as excinfo:
        OpenAIAdapter()
    message = str(excinfo.value)
    assert "OpenAIAdapter" in message
    assert DOCS_REF in message


@pytest.mark.unit
def test_gemini_adapter_no_args_raises() -> None:
    with pytest.raises(CredentialError) as excinfo:
        GeminiAdapter()
    message = str(excinfo.value)
    assert "GeminiAdapter" in message
    assert DOCS_REF in message


@pytest.mark.unit
def test_claude_adapter_credentials_path_calls_provider() -> None:
    provider = _RecordingProvider({"x-api-key": "from-provider"})
    ClaudeAdapter(credentials=provider)
    assert provider.calls == ["claude"]


@pytest.mark.unit
def test_openai_adapter_credentials_path_calls_provider() -> None:
    provider = _RecordingProvider({"authorization": "Bearer from-provider"})
    OpenAIAdapter(credentials=provider)
    assert provider.calls == ["openai"]


@pytest.mark.unit
def test_gemini_adapter_credentials_path_calls_provider() -> None:
    provider = _RecordingProvider({"x-goog-api-key": "from-provider"})
    GeminiAdapter(credentials=provider)
    assert provider.calls == ["gemini"]


@pytest.mark.unit
def test_claude_adapter_api_key_does_not_call_provider() -> None:
    ClaudeAdapter(api_key="sk-ant", credentials=_ExplodingProvider())


@pytest.mark.unit
def test_openai_adapter_api_key_does_not_call_provider() -> None:
    OpenAIAdapter(api_key="sk-oai", credentials=_ExplodingProvider())


@pytest.mark.unit
def test_gemini_adapter_api_key_does_not_call_provider() -> None:
    GeminiAdapter(api_key="gg", credentials=_ExplodingProvider())


@pytest.mark.unit
def test_claude_adapter_client_bypasses_provider() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    client = httpx.AsyncClient(transport=transport)
    ClaudeAdapter(client=client, credentials=_ExplodingProvider())


@pytest.mark.unit
def test_openai_adapter_client_bypasses_provider() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    client = httpx.AsyncClient(transport=transport)
    OpenAIAdapter(client=client, credentials=_ExplodingProvider())


@pytest.mark.unit
def test_gemini_adapter_client_bypasses_provider() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    client = httpx.AsyncClient(transport=transport)
    GeminiAdapter(client=client, credentials=_ExplodingProvider())


# OAuthProvider --------------------------------------------------------
def _write_oauth_store(
    path: Path,
    *,
    backend: str = "claude",
    access_token: str = "cached-tok",
    refresh_token: str = "refresh-tok",
    token_url: str = "https://auth.example/token",
    client_id: str = "client-abc",
    client_secret: str | None = None,
    expires_at: float = 0.0,
) -> None:
    entry: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_url": token_url,
        "client_id": client_id,
        "expires_at": expires_at,
    }
    if client_secret is not None:
        entry["client_secret"] = client_secret
    path.write_text(json.dumps({backend: entry}), encoding="utf-8")


def _exploding_transport() -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("token endpoint must not be called on this path")

    return httpx.MockTransport(_handler)


@pytest.mark.unit
def test_oauth_cached_token_skips_refresh(tmp_path: Path) -> None:
    store = tmp_path / "oauth_tokens.json"
    _write_oauth_store(store, access_token="cached-tok", expires_at=1000.0)
    client = httpx.Client(transport=_exploding_transport())
    provider = OAuthProvider(store_path=store, client=client, now=lambda: 500.0)
    cred = provider.resolve("claude")
    assert cred == Credential(headers={"authorization": "Bearer cached-tok"})


@pytest.mark.unit
def test_oauth_expired_token_triggers_refresh(tmp_path: Path) -> None:
    store = tmp_path / "oauth_tokens.json"
    _write_oauth_store(
        store,
        access_token="stale-tok",
        refresh_token="refresh-tok",
        token_url="https://auth.example/token",
        client_id="client-abc",
        expires_at=1000.0,
    )
    recorder: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        recorder["url"] = str(request.url)
        recorder["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"access_token": "new-tok", "expires_in": 3600})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    provider = OAuthProvider(store_path=store, client=client, now=lambda: 5000.0)
    cred = provider.resolve("claude")
    assert cred == Credential(headers={"authorization": "Bearer new-tok"})
    assert recorder["url"] == "https://auth.example/token"
    assert "grant_type=refresh_token" in recorder["body"]
    assert "refresh_token=refresh-tok" in recorder["body"]
    assert "client_id=client-abc" in recorder["body"]
    persisted = json.loads(store.read_text(encoding="utf-8"))["claude"]
    assert persisted["access_token"] == "new-tok"
    assert persisted["expires_at"] == 5000.0 + 3600
    assert (store.stat().st_mode & 0o777) == 0o600


@pytest.mark.unit
def test_oauth_unprimed_store_raises(tmp_path: Path) -> None:
    store = tmp_path / "missing.json"
    provider = OAuthProvider(
        store_path=store,
        client=httpx.Client(transport=_exploding_transport()),
        now=lambda: 0.0,
    )
    with pytest.raises(CredentialError) as excinfo:
        provider.resolve("claude")
    message = str(excinfo.value)
    assert "prompiler login" in message
    assert DOCS_REF in message
    assert "\n" not in message


@pytest.mark.unit
def test_oauth_missing_backend_entry_raises(tmp_path: Path) -> None:
    store = tmp_path / "oauth_tokens.json"
    _write_oauth_store(store, backend="openai")
    provider = OAuthProvider(
        store_path=store,
        client=httpx.Client(transport=_exploding_transport()),
        now=lambda: 0.0,
    )
    with pytest.raises(CredentialError) as excinfo:
        provider.resolve("claude")
    message = str(excinfo.value)
    assert "prompiler login" in message
    assert DOCS_REF in message
    assert "\n" not in message


@pytest.mark.unit
def test_oauth_refresh_http_error_raises(tmp_path: Path) -> None:
    store = tmp_path / "oauth_tokens.json"
    _write_oauth_store(store, expires_at=1000.0)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    provider = OAuthProvider(store_path=store, client=client, now=lambda: 5000.0)
    with pytest.raises(CredentialError) as excinfo:
        provider.resolve("claude")
    message = str(excinfo.value)
    assert DOCS_REF in message
    assert "\n" not in message


@pytest.mark.unit
def test_oauth_refresh_missing_access_token_raises(tmp_path: Path) -> None:
    store = tmp_path / "oauth_tokens.json"
    _write_oauth_store(store, expires_at=1000.0)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 3600})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    provider = OAuthProvider(store_path=store, client=client, now=lambda: 5000.0)
    with pytest.raises(CredentialError) as excinfo:
        provider.resolve("claude")
    message = str(excinfo.value)
    assert DOCS_REF in message
    assert "\n" not in message
