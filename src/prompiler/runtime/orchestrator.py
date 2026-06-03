"""Runtime orchestrator — PLAN.md L160-L162 (fat 4b unit).

Public surface (architecture.md L116-117):

- ``run(name, text, *, backend, registry=None, timeout=None) -> BaseModel``
- ``run_sync(name, text, *, backend, registry=None, timeout=None) -> BaseModel``
- ``run_batch(name, texts, *, backend, registry=None, concurrency=8,
  timeout=None) -> list[BaseModel | Exception]``

Pipeline per architecture.md L266:

1. Registry lookup — raises ``KeyError`` on unknown name (LL-004 — orchestrator
   surfaces the registry's raise verbatim; no swallow, no fallback).
2. Doc-size guardrail (L162) — char-count heuristic at ``_CHARS_PER_TOKEN`` chars
   per token. ``ArtefactBundle.max_input_tokens is None`` disables the check.
   Skipping the vendor tokenizer here is deliberate: a real tokenizer would
   couple this module to a specific backend SDK, defeating the per-backend
   adapter Protocol.
3. Prompt assembly (D4) — append a literal ``## Input\n`` block. The header
   constant carries the leading double newline so the synthesized prompt body
   ends with a clean separator regardless of trailing whitespace upstream.
4. Adapter dispatch via the ``BackendAdapter`` Protocol (P2.1 design lock B —
   async from day one).
5. ``model_validate`` — on success return the typed instance; on
   ``ValidationError`` re-attempt once with the corrective-feedback prompt
   prepended. Two failed attempts raise ``ExtractionFailed from second_err``
   (D5) so the originating Pydantic error survives via ``__cause__``.

Note that transient retry (network blips, 5xx) lives at the adapter layer
(``backends/retry.py``). The orchestrator's retry budget is reserved for
validation errors — mixing the two budgets here would let a flaky adapter
silently exhaust the validation retry and surface as ``ExtractionFailed``
when the real fault was network-layer.

``run_batch`` enforces concurrency via ``asyncio.Semaphore`` and isolates
per-item failures: an exception from any one item is captured and placed in
the result list at the matching index so the caller sees one slot per input
in submission order.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ValidationError

from prompiler.backends.base import BackendAdapter
from prompiler.runtime.errors import ExtractionFailed
from prompiler.runtime.registry import Registry, _resolve

__all__ = ["run", "run_batch", "run_sync"]

_INPUT_BLOCK_HEADER: Final[str] = "\n\n## Input\n"
_CORRECTIVE_FEEDBACK_HEADER: Final[str] = (
    "Previous attempt failed validation. Re-emit JSON that matches the schema "
    "exactly.\nValidation error:\n"
)
_CHARS_PER_TOKEN: Final[int] = 4
_DEFAULT_BATCH_CONCURRENCY: Final[int] = 8


def _assemble_prompt(base_prompt: str, text: str) -> str:
    return f"{base_prompt}{_INPUT_BLOCK_HEADER}{text}"


def _corrective_prompt(base_prompt: str, validation_err: ValidationError) -> str:
    return f"{_CORRECTIVE_FEEDBACK_HEADER}{validation_err}\n\n{base_prompt}"


def _check_doc_size(text: str, max_input_tokens: int | None) -> None:
    if max_input_tokens is None:
        return
    if len(text) > max_input_tokens * _CHARS_PER_TOKEN:
        raise ExtractionFailed(
            f"input exceeds doc-size guardrail: "
            f"{len(text)} chars > {max_input_tokens} tokens "
            f"* {_CHARS_PER_TOKEN} chars/token"
        )


async def run(
    name: str,
    text: str,
    *,
    backend: BackendAdapter,
    registry: Registry | None = None,
    timeout: float | None = None,
) -> BaseModel:
    """Run extraction for a single document. See module docstring."""
    bundle = _resolve(registry).get(name)
    _check_doc_size(text, bundle.max_input_tokens)

    json_schema = bundle.pydantic_cls.model_json_schema()
    prompt = _assemble_prompt(bundle.prompt, text)
    raw = await backend.extract(prompt=prompt, json_schema=json_schema, timeout=timeout)
    try:
        return bundle.pydantic_cls.model_validate(raw)
    except ValidationError as first_err:
        retry_prompt = _corrective_prompt(prompt, first_err)
        raw = await backend.extract(prompt=retry_prompt, json_schema=json_schema, timeout=timeout)
        try:
            return bundle.pydantic_cls.model_validate(raw)
        except ValidationError as second_err:
            raise ExtractionFailed(
                f"validation failed after retry for spec {name!r}"
            ) from second_err


def run_sync(
    name: str,
    text: str,
    *,
    backend: BackendAdapter,
    registry: Registry | None = None,
    timeout: float | None = None,
) -> BaseModel:
    """Sync wrapper over :func:`run` via :func:`asyncio.run`."""
    return asyncio.run(run(name, text, backend=backend, registry=registry, timeout=timeout))


async def run_batch(
    name: str,
    texts: Sequence[str],
    *,
    backend: BackendAdapter,
    registry: Registry | None = None,
    concurrency: int = _DEFAULT_BATCH_CONCURRENCY,
    timeout: float | None = None,
) -> list[BaseModel | Exception]:
    """Run extraction over many documents with per-item isolation.

    Caps in-flight calls at ``concurrency`` via ``asyncio.Semaphore``. Returns
    one entry per input in submission order; per-item exceptions are captured
    rather than re-raised so a single failure does not abort the batch.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(text: str) -> BaseModel | Exception:
        async with semaphore:
            try:
                return await run(
                    name,
                    text,
                    backend=backend,
                    registry=registry,
                    timeout=timeout,
                )
            except Exception as err:
                return err

    return await asyncio.gather(*(_run_one(t) for t in texts))
