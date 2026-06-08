"""Doc-size chunking helper — PRD FR-13.

``prompiler`` does not auto-chunk inside ``run()`` (FR-13: the caller owns
chunking). This module ships the utility a caller reaches for when a document
exceeds a spec's ``max_input_tokens`` guardrail.

The token estimate is the same char-count heuristic the orchestrator's
``_check_doc_size`` uses (``_CHARS_PER_TOKEN`` chars per token), so every chunk
this returns is guaranteed to clear that guardrail. Reusing the constant keeps
the two in lockstep: a vendor tokenizer here would couple the helper to a
specific backend SDK, defeating the per-backend adapter Protocol.

Splitting is char-window based and may cut mid-word. Set ``overlap_tokens`` to
re-include trailing context at each chunk boundary so an entity spanning the
cut is recoverable from the following chunk.
"""

from __future__ import annotations

from prompiler.runtime.orchestrator import _CHARS_PER_TOKEN

__all__ = ["chunk_for_extract"]


def chunk_for_extract(
    text: str,
    max_input_tokens: int,
    *,
    overlap_tokens: int = 0,
) -> list[str]:
    """Split ``text`` into chunks that each clear the doc-size guardrail.

    Each chunk is at most ``max_input_tokens * _CHARS_PER_TOKEN`` characters,
    so it passes the orchestrator's ``_check_doc_size`` for the same
    ``max_input_tokens``. Adjacent chunks overlap by
    ``overlap_tokens * _CHARS_PER_TOKEN`` characters.

    Empty input yields an empty list; input already within the guardrail is
    returned as a single chunk.
    """
    if max_input_tokens < 1:
        raise ValueError("max_input_tokens must be >= 1")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens must be >= 0")
    if overlap_tokens >= max_input_tokens:
        raise ValueError("overlap_tokens must be < max_input_tokens")

    if not text:
        return []

    max_chars = max_input_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return [text]

    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN
    stride = max_chars - overlap_chars

    chunks: list[str] = []
    start = 0
    n = len(text)
    while True:
        end = start + max_chars
        chunks.append(text[start:end])
        if end >= n:
            break
        start += stride
    return chunks
