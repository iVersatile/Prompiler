from __future__ import annotations

import json

import pytest

from _cassette import (
    REDACTED_VALUE,
    CassetteExhausted,
    CassetteInteraction,
    CassettePlayer,
    CassetteRecorder,
    redact_headers,
    serialize_interaction,
)


@pytest.mark.unit
def test_redact_headers_strips_credentials_case_insensitive() -> None:
    headers = {
        "Authorization": "Bearer secret-token",
        "X-API-Key": "sk-abc123",
        "Content-Type": "application/json",
        "Cookie": "session=xyz",
    }

    redacted = redact_headers(headers)

    assert redacted["Authorization"] == REDACTED_VALUE
    assert redacted["X-API-Key"] == REDACTED_VALUE
    assert redacted["Cookie"] == REDACTED_VALUE
    assert redacted["Content-Type"] == "application/json"


@pytest.mark.unit
def test_serialize_redacts_credentials_in_request_and_response_headers() -> None:
    interaction = CassetteInteraction(
        method="POST",
        url="https://api.example.com/v1/messages",
        request_headers={
            "authorization": "Bearer s3cr3t",
            "content-type": "application/json",
        },
        request_body=b'{"prompt": "hi"}',
        status=200,
        response_headers={"set-cookie": "id=abc", "content-type": "application/json"},
        response_body=b'{"ok": true}',
    )

    payload = serialize_interaction(interaction)

    assert payload["request_headers"]["authorization"] == REDACTED_VALUE
    assert payload["response_headers"]["set-cookie"] == REDACTED_VALUE
    assert payload["request_headers"]["content-type"] == "application/json"


@pytest.mark.unit
def test_record_roundtrip_preserves_body_bytes() -> None:
    original = CassetteInteraction(
        method="POST",
        url="https://api.example.com/v1/messages",
        request_headers={"content-type": "application/json"},
        request_body=b'{"prompt": "hello \xf0\x9f\x91\x8b"}',
        status=200,
        response_headers={"content-type": "application/json"},
        response_body=b"\x00\x01\x02 binary blob \xff",
    )
    recorder = CassetteRecorder()
    recorder.record(original)

    raw = recorder.to_json()
    json.loads(raw)
    player = CassettePlayer.from_json(raw)
    replayed = player.next()

    assert replayed.request_body == original.request_body
    assert replayed.response_body == original.response_body
    assert replayed.method == original.method
    assert replayed.url == original.url
    assert replayed.status == original.status


@pytest.mark.unit
def test_player_raises_when_exhausted() -> None:
    interaction = CassetteInteraction(
        method="GET",
        url="https://api.example.com/ping",
        request_headers={},
        request_body=b"",
        status=200,
        response_headers={},
        response_body=b"pong",
    )
    recorder = CassetteRecorder()
    recorder.record(interaction)
    player = CassettePlayer.from_json(recorder.to_json())

    player.next()

    assert player.remaining == 0
    with pytest.raises(CassetteExhausted):
        player.next()
