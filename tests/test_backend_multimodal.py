"""A4 — modal content threading through backend adapters (RED).

Source of truth: docs/PLAN.md §Q1 Track A task A4 exit criterion —
"per-adapter payload tests assert correct modal block shape; unsupported
backend raises a clear capability error, not a silent drop."

Scope is the adapter layer only: ``extract`` gains a keyword-only
``modal_parts`` argument; each adapter projects those parts into its vendor
payload dialect and gates on capability. The orchestrator/MCP plumbing that
*supplies* modal parts is a later task (A5) and is not exercised here.

The shared cassette transport (``_cassette_transport.make_cassette_transport``)
asserts only method + URL and never inspects the request body, so payload-shape
assertions use a local request-capturing transport that records each outgoing
request and replays the adapter's recorded happy-path response so ``extract``
parsing still succeeds.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import httpx
import pytest

from _cassette import CassettePlayer
from prompiler.backends import (
    CapabilityError,
    ClaudeAdapter,
    GeminiAdapter,
    ModalContent,
    OllamaAdapter,
    OpenAIAdapter,
)
from prompiler.backends.claude import ANTHROPIC_BASE_URL
from prompiler.backends.gemini import GEMINI_BASE_URL
from prompiler.backends.mock import MockAdapter
from prompiler.backends.ollama import OLLAMA_BASE_URL
from prompiler.backends.openai import OPENAI_BASE_URL

CASSETTE_DIR = Path(__file__).parent / "cassettes"

IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake-png-bytes"
IMAGE_B64 = base64.b64encode(IMAGE_BYTES).decode("ascii")

AUDIO_BYTES = b"RIFFfake-wav-bytes"
AUDIO_B64 = base64.b64encode(AUDIO_BYTES).decode("ascii")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}
PROMPT = "Classify the following text into one routing bucket."


def _image_part() -> ModalContent:
    return ModalContent(modality="image", media_type="image/png", data=IMAGE_BYTES)


def _audio_part() -> ModalContent:
    return ModalContent(modality="audio", media_type="audio/wav", data=AUDIO_BYTES)


def _capturing_transport(cassette_name: str, captured: list[httpx.Request]) -> httpx.MockTransport:
    """Record each request, replay the recorded happy-path response in order."""
    player = CassettePlayer.from_json((CASSETTE_DIR / cassette_name).read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        interaction = player.next()
        return httpx.Response(
            status_code=interaction.status,
            headers=interaction.response_headers,
            content=interaction.response_body,
        )

    return httpx.MockTransport(handler)


@pytest.mark.unit
def test_modal_content_fields_and_frozen() -> None:
    part = _image_part()
    assert part.modality == "image"
    assert part.media_type == "image/png"
    assert part.data == IMAGE_BYTES
    with pytest.raises(FrozenInstanceError):
        part.modality = "audio"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [
        lambda: MockAdapter(),
        lambda: ClaudeAdapter(client=httpx.AsyncClient(base_url=ANTHROPIC_BASE_URL)),
        lambda: OpenAIAdapter(client=httpx.AsyncClient(base_url=OPENAI_BASE_URL)),
        lambda: GeminiAdapter(client=httpx.AsyncClient(base_url=GEMINI_BASE_URL)),
        lambda: OllamaAdapter(client=httpx.AsyncClient(base_url=OLLAMA_BASE_URL)),
    ],
    ids=["mock", "claude", "openai", "gemini", "ollama"],
)
def test_supports_multimodal_true_for_all(factory: Any) -> None:
    assert factory().supports("multimodal") is True


@pytest.mark.unit
def test_claude_threads_image_block_into_content() -> None:
    captured: list[httpx.Request] = []
    client = httpx.AsyncClient(
        transport=_capturing_transport("claude_happy_path.json", captured),
        base_url=ANTHROPIC_BASE_URL,
    )
    adapter = ClaudeAdapter(client=client)
    asyncio.run(adapter.extract(prompt=PROMPT, json_schema=SCHEMA, modal_parts=[_image_part()]))
    content = json.loads(captured[0].content)["messages"][0]["content"]
    assert isinstance(content, list)
    assert {"type": "text", "text": PROMPT} in content
    assert {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": IMAGE_B64,
        },
    } in content


@pytest.mark.unit
def test_openai_threads_image_url_data_uri() -> None:
    captured: list[httpx.Request] = []
    client = httpx.AsyncClient(
        transport=_capturing_transport("openai_happy_path.json", captured),
        base_url=OPENAI_BASE_URL,
    )
    adapter = OpenAIAdapter(client=client)
    asyncio.run(adapter.extract(prompt=PROMPT, json_schema=SCHEMA, modal_parts=[_image_part()]))
    content = json.loads(captured[0].content)["messages"][0]["content"]
    assert isinstance(content, list)
    assert {"type": "text", "text": PROMPT} in content
    assert {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{IMAGE_B64}"},
    } in content


@pytest.mark.unit
def test_gemini_threads_inline_data_part() -> None:
    captured: list[httpx.Request] = []
    client = httpx.AsyncClient(
        transport=_capturing_transport("gemini_happy_path.json", captured),
        base_url=GEMINI_BASE_URL,
    )
    adapter = GeminiAdapter(client=client)
    asyncio.run(adapter.extract(prompt=PROMPT, json_schema=SCHEMA, modal_parts=[_image_part()]))
    parts = json.loads(captured[0].content)["contents"][0]["parts"]
    assert {"text": PROMPT} in parts
    assert {"inlineData": {"mimeType": "image/png", "data": IMAGE_B64}} in parts


@pytest.mark.unit
def test_ollama_threads_images_array() -> None:
    captured: list[httpx.Request] = []
    client = httpx.AsyncClient(
        transport=_capturing_transport("ollama_happy_path.json", captured),
        base_url=OLLAMA_BASE_URL,
    )
    adapter = OllamaAdapter(client=client)
    asyncio.run(adapter.extract(prompt=PROMPT, json_schema=SCHEMA, modal_parts=[_image_part()]))
    message = json.loads(captured[0].content)["messages"][0]
    assert message["images"] == [IMAGE_B64]


@pytest.mark.unit
def test_gemini_threads_audio_inline_data() -> None:
    captured: list[httpx.Request] = []
    client = httpx.AsyncClient(
        transport=_capturing_transport("gemini_happy_path.json", captured),
        base_url=GEMINI_BASE_URL,
    )
    adapter = GeminiAdapter(client=client)
    asyncio.run(adapter.extract(prompt=PROMPT, json_schema=SCHEMA, modal_parts=[_audio_part()]))
    parts = json.loads(captured[0].content)["contents"][0]["parts"]
    assert {"inlineData": {"mimeType": "audio/wav", "data": AUDIO_B64}} in parts


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [
        lambda: MockAdapter(),
        lambda: ClaudeAdapter(client=httpx.AsyncClient(base_url=ANTHROPIC_BASE_URL)),
        lambda: OpenAIAdapter(client=httpx.AsyncClient(base_url=OPENAI_BASE_URL)),
        lambda: OllamaAdapter(client=httpx.AsyncClient(base_url=OLLAMA_BASE_URL)),
    ],
    ids=["mock", "claude", "openai", "ollama"],
)
def test_audio_raises_capability_error_on_non_gemini(factory: Any) -> None:
    adapter = factory()
    with pytest.raises(CapabilityError):
        asyncio.run(adapter.extract(prompt=PROMPT, json_schema=SCHEMA, modal_parts=[_audio_part()]))
