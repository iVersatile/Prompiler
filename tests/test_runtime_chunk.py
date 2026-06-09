"""Unit tests for ``chunk_for_extract`` — PRD FR-13 doc-size chunking helper.

Every chunk the helper returns must clear the orchestrator's doc-size
guardrail for the same ``max_input_tokens``; the boundary assertions below
reuse ``_CHARS_PER_TOKEN`` so the test stays in lockstep with the heuristic.
"""

from __future__ import annotations

import pytest

from prompiler.runtime import chunk_for_extract
from prompiler.runtime.orchestrator import _CHARS_PER_TOKEN


@pytest.mark.unit
def test_empty_text_yields_empty_list() -> None:
    # Arrange / Act
    chunks = chunk_for_extract("", max_input_tokens=4)

    # Assert
    assert chunks == []


@pytest.mark.unit
def test_short_text_returned_as_single_chunk() -> None:
    # Arrange
    text = "abc"

    # Act
    chunks = chunk_for_extract(text, max_input_tokens=4)

    # Assert
    assert chunks == [text]


@pytest.mark.unit
def test_text_exactly_at_boundary_is_single_chunk() -> None:
    # Arrange — len == max_input_tokens * _CHARS_PER_TOKEN
    max_input_tokens = 3
    text = "x" * (max_input_tokens * _CHARS_PER_TOKEN)

    # Act
    chunks = chunk_for_extract(text, max_input_tokens=max_input_tokens)

    # Assert
    assert chunks == [text]


@pytest.mark.unit
def test_oversized_text_splits_into_multiple_chunks() -> None:
    # Arrange
    max_input_tokens = 2  # max_chars == 8
    text = "0123456789ABCDEFGHIJ"  # len 20

    # Act
    chunks = chunk_for_extract(text, max_input_tokens=max_input_tokens)

    # Assert
    assert chunks == ["01234567", "89ABCDEF", "GHIJ"]


@pytest.mark.unit
def test_no_overlap_chunks_reconstruct_original() -> None:
    # Arrange
    max_input_tokens = 2
    text = "0123456789ABCDEFGHIJ"

    # Act
    chunks = chunk_for_extract(text, max_input_tokens=max_input_tokens)

    # Assert
    assert "".join(chunks) == text


@pytest.mark.unit
def test_overlap_reincludes_trailing_context() -> None:
    # Arrange — max_chars 8, overlap_chars 4, stride 4
    text = "ABCDEFGHIJKL"  # len 12

    # Act
    chunks = chunk_for_extract(text, max_input_tokens=2, overlap_tokens=1)

    # Assert
    assert chunks == ["ABCDEFGH", "EFGHIJKL"]
    assert chunks[1].startswith(chunks[0][-4:])


@pytest.mark.unit
def test_every_chunk_clears_doc_size_guardrail() -> None:
    # Arrange
    max_input_tokens = 5
    max_chars = max_input_tokens * _CHARS_PER_TOKEN
    text = "y" * 137

    # Act
    chunks = chunk_for_extract(text, max_input_tokens=max_input_tokens, overlap_tokens=2)

    # Assert — each chunk is within the guardrail _check_doc_size enforces
    assert chunks
    assert all(len(chunk) <= max_chars for chunk in chunks)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -1])
def test_max_input_tokens_below_one_raises(bad: int) -> None:
    with pytest.raises(ValueError, match="max_input_tokens must be >= 1"):
        chunk_for_extract("abc", max_input_tokens=bad)


@pytest.mark.unit
def test_negative_overlap_raises() -> None:
    with pytest.raises(ValueError, match="overlap_tokens must be >= 0"):
        chunk_for_extract("abc", max_input_tokens=4, overlap_tokens=-1)


@pytest.mark.unit
def test_overlap_not_less_than_max_input_tokens_raises() -> None:
    with pytest.raises(ValueError, match="overlap_tokens must be < max_input_tokens"):
        chunk_for_extract("abc", max_input_tokens=4, overlap_tokens=4)
