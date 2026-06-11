"""CLI ``prompiler login`` — primes the OAuth token store headlessly.

The interactive grant is modelled as environment-fed input so tests stay
deterministic and never hit the network. ``login`` reads the access/refresh
token, token URL, and client id from ``PROMPILER_OAUTH_*`` env vars and writes
a per-backend entry into the token store that :class:`OAuthProvider` later
serves. Secrets must never be echoed to stdout/stderr (PRD FR-10).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from prompiler.cli import main

_ACCESS = "primed-access-tok"
_REFRESH = "primed-refresh-tok"
_TOKEN_URL = "https://auth.example/token"
_CLIENT_ID = "client-abc"
_CLIENT_SECRET = "secret-xyz"

_REQUIRED_ENV = {
    "PROMPILER_OAUTH_ACCESS_TOKEN": _ACCESS,
    "PROMPILER_OAUTH_REFRESH_TOKEN": _REFRESH,
    "PROMPILER_OAUTH_TOKEN_URL": _TOKEN_URL,
    "PROMPILER_OAUTH_CLIENT_ID": _CLIENT_ID,
}


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "oauth_tokens.json"


def _set_required(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> None:
    monkeypatch.setenv("PROMPILER_OAUTH_STORE", str(store_path))
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PROMPILER_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("PROMPILER_OAUTH_EXPIRES_IN", raising=False)


@pytest.mark.unit
def test_login_primes_store(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> None:
    _set_required(monkeypatch, store_path)

    rc = main(["login", "claude"])

    assert rc == 0
    store = json.loads(store_path.read_text(encoding="utf-8"))
    entry = store["claude"]
    assert entry["access_token"] == _ACCESS
    assert entry["refresh_token"] == _REFRESH
    assert entry["token_url"] == _TOKEN_URL
    assert entry["client_id"] == _CLIENT_ID
    assert entry["expires_at"] > 0


@pytest.mark.unit
def test_login_never_echoes_token(
    monkeypatch: pytest.MonkeyPatch,
    store_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required(monkeypatch, store_path)
    monkeypatch.setenv("PROMPILER_OAUTH_CLIENT_SECRET", _CLIENT_SECRET)

    rc = main(["login", "claude"])

    assert rc == 0
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert _ACCESS not in blob
    assert _REFRESH not in blob
    assert _CLIENT_SECRET not in blob


@pytest.mark.unit
def test_login_writes_owner_only_store(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> None:
    _set_required(monkeypatch, store_path)

    rc = main(["login", "claude"])

    assert rc == 0
    mode = stat.S_IMODE(store_path.stat().st_mode)
    assert mode == 0o600


@pytest.mark.unit
def test_login_merges_existing_backends(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> None:
    store_path.write_text(
        json.dumps({"openai": {"access_token": "pre-existing"}}),
        encoding="utf-8",
    )
    _set_required(monkeypatch, store_path)

    rc = main(["login", "claude"])

    assert rc == 0
    store = json.loads(store_path.read_text(encoding="utf-8"))
    assert store["openai"]["access_token"] == "pre-existing"
    assert store["claude"]["access_token"] == _ACCESS


@pytest.mark.unit
def test_login_missing_required_env_is_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    store_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PROMPILER_OAUTH_STORE", str(store_path))
    for key in _REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)

    rc = main(["login", "claude"])

    assert rc == 2
    assert not store_path.exists()
    assert "PROMPILER_OAUTH_ACCESS_TOKEN" in capsys.readouterr().err


@pytest.mark.unit
def test_login_unknown_backend_is_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    store_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required(monkeypatch, store_path)

    rc = main(["login", "nope"])

    assert rc == 2
    assert "nope" in capsys.readouterr().err


@pytest.mark.unit
def test_login_bad_expires_in_is_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    store_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required(monkeypatch, store_path)
    monkeypatch.setenv("PROMPILER_OAUTH_EXPIRES_IN", "not-a-number")

    rc = main(["login", "claude"])

    assert rc == 2
    assert "PROMPILER_OAUTH_EXPIRES_IN" in capsys.readouterr().err
