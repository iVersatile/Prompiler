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
import hashlib
import os
import tomllib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from prompiler import obs
from prompiler.backends.base import (
    BackendAdapter,
    ExtractResult,
    ModalContent,
    StreamEvent,
    StreamingBackendAdapter,
)
from prompiler.runtime.errors import ExtractionFailed
from prompiler.runtime.registry import Registry, _resolve

__all__ = [
    "result_cache_clear",
    "result_cache_info",
    "run",
    "run_batch",
    "run_stream",
    "run_sync",
]

_INPUT_BLOCK_HEADER: Final[str] = "\n\n## Input\n"
_CORRECTIVE_FEEDBACK_HEADER: Final[str] = (
    "Previous attempt failed validation. Re-emit JSON that matches the schema "
    "exactly.\nValidation error:\n"
)
_CHARS_PER_TOKEN: Final[int] = 4
_DEFAULT_BATCH_CONCURRENCY: Final[int] = 8

# Determinism config (FR-2, FR-14). Precedence mirrors registry._resolve_prompts_dir:
# kwarg -> env var -> pyproject.toml [tool.prompiler] -> hardcoded default.
_TEMPERATURE_ENV: Final[str] = "PROMPILER_TEMPERATURE"
_SEED_ENV: Final[str] = "PROMPILER_SEED"
_DISABLE_CACHE_ENV: Final[str] = "PROMPILER_DISABLE_CACHE"
_DEFAULT_TEMPERATURE: Final[float] = 0.0
_DEFAULT_SEED: Final[int] = 42
_DEFAULT_DISABLE_CACHE: Final[bool] = False

_logger = obs.get_logger("prompiler.orchestrator")


class _Unset:
    """Sentinel distinguishing "kwarg not provided" from an explicit ``seed=None``."""


_UNSET: Final[_Unset] = _Unset()

# Process-lifetime set of backend class names already warned for non-determinism.
# One WARN per non-seed backend per process (FR-14) — not per call.
_warned_backends: set[str] = set()


@dataclass(frozen=True)
class ResultCacheInfo:
    """Observable counters for the runtime result cache (FR-14)."""

    hits: int
    misses: int
    currsize: int


# Runtime result cache (B2 / FR-14). Keyed on the 5-tuple
# ``(spec_hash, backend_class, model, input_hash, prompt_hash)``. The cached
# value is the *validated* model, not the raw ``ExtractResult`` — a hit
# short-circuits before the doc-size guardrail, determinism config, and adapter
# dispatch, and a failing first attempt is never stored so it cannot poison the
# validation-retry path. Backend identity mirrors ``_warn_if_nondeterministic``
# (class name) plus the adapter's ``_model`` attribute when present (``None``
# otherwise). ``prompt_hash`` covers ``bundle.prompt`` because the refine loop
# patches the prompt via ``dataclasses.replace`` while leaving ``spec_hash``
# constant; without it a hit would return the pre-refine degraded extraction.
_RESULT_CACHE: dict[tuple[str, str, str | None, str, str], BaseModel] = {}
_RESULT_CACHE_STATS: dict[str, int] = {"hits": 0, "misses": 0}


def _backend_identity(backend: BackendAdapter) -> tuple[str, str | None]:
    model = getattr(backend, "_model", None)
    return type(backend).__name__, model


def _input_hash(text: str, modal_parts: Sequence[ModalContent]) -> str:
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    for part in modal_parts:
        h.update(part.media_type.encode("utf-8"))
        h.update(part.data)
    return h.hexdigest()


def _result_cache_key(
    spec_hash: str,
    backend: BackendAdapter,
    text: str,
    modal_parts: Sequence[ModalContent],
    prompt: str,
) -> tuple[str, str, str | None, str, str]:
    backend_class, model = _backend_identity(backend)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return (
        spec_hash,
        backend_class,
        model,
        _input_hash(text, modal_parts),
        prompt_hash,
    )


def result_cache_info() -> ResultCacheInfo:
    """Return current result-cache hit/miss counts and resident entry count."""
    return ResultCacheInfo(
        hits=_RESULT_CACHE_STATS["hits"],
        misses=_RESULT_CACHE_STATS["misses"],
        currsize=len(_RESULT_CACHE),
    )


def result_cache_clear() -> None:
    """Empty the result cache and zero the hit/miss counters (test isolation)."""
    _RESULT_CACHE.clear()
    _RESULT_CACHE_STATS["hits"] = 0
    _RESULT_CACHE_STATS["misses"] = 0


def _read_prompiler_block() -> dict[str, Any]:
    """Return ``[tool.prompiler]`` from cwd ``pyproject.toml``; ``{}`` if absent."""
    path = Path.cwd() / "pyproject.toml"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    block = data.get("tool", {}).get("prompiler", {})
    return block if isinstance(block, dict) else {}


def _resolve_temperature(temperature: float | _Unset, block: dict[str, Any]) -> float:
    if not isinstance(temperature, _Unset):
        return temperature
    env = os.environ.get(_TEMPERATURE_ENV)
    if env is not None:
        return float(env)
    if "temperature" in block:
        return float(block["temperature"])
    return _DEFAULT_TEMPERATURE


def _resolve_seed(seed: int | None | _Unset, block: dict[str, Any]) -> int | None:
    if not isinstance(seed, _Unset):
        return seed
    env = os.environ.get(_SEED_ENV)
    if env is not None:
        return None if env.strip().lower() in ("", "none", "null") else int(env)
    if "seed" in block:
        raw = block["seed"]
        return None if raw is None else int(raw)
    return _DEFAULT_SEED


def _resolve_cache_disabled(disabled: bool | _Unset, block: dict[str, Any]) -> bool:
    if not isinstance(disabled, _Unset):
        return disabled
    env = os.environ.get(_DISABLE_CACHE_ENV)
    if env is not None:
        return env == "1"
    if "disable_cache" in block:
        return bool(block["disable_cache"])
    return _DEFAULT_DISABLE_CACHE


def _warn_if_nondeterministic(backend: BackendAdapter) -> None:
    """Emit one WARN per non-seed backend per process (FR-14)."""
    if backend.supports("seed"):
        return
    key = type(backend).__name__
    if key in _warned_backends:
        return
    _warned_backends.add(key)
    _logger.warning(
        "backend does not support seed; runs are not byte-reproducible",
        extra={"event": "nondeterministic_backend", "backend": key},
    )


def _trace_deterministic(result: ExtractResult) -> None:
    _logger.log(
        obs.TRACE,
        "extraction deterministic flag",
        extra={
            "event": "deterministic",
            "deterministic": result.deterministic,
            "system_fingerprint": result.system_fingerprint,
        },
    )


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
    temperature: float | _Unset = _UNSET,
    seed: int | None | _Unset = _UNSET,
    modal_parts: Sequence[ModalContent] = (),
    disable_cache: bool | _Unset = _UNSET,
) -> BaseModel:
    """Run extraction for a single document. See module docstring."""
    bundle = _resolve(registry).get(name)
    block = _read_prompiler_block()
    cache_disabled = _resolve_cache_disabled(disable_cache, block)

    cache_key = _result_cache_key(bundle.spec_hash, backend, text, modal_parts, bundle.prompt)
    if not cache_disabled:
        cached = _RESULT_CACHE.get(cache_key)
        if cached is not None:
            _RESULT_CACHE_STATS["hits"] += 1
            return cached
        _RESULT_CACHE_STATS["misses"] += 1

    _check_doc_size(text, bundle.max_input_tokens)

    resolved_temperature = _resolve_temperature(temperature, block)
    resolved_seed = _resolve_seed(seed, block)
    _warn_if_nondeterministic(backend)

    json_schema = bundle.pydantic_cls.model_json_schema()
    prompt = _assemble_prompt(bundle.prompt, text)
    result = await backend.extract(
        prompt=prompt,
        json_schema=json_schema,
        timeout=timeout,
        temperature=resolved_temperature,
        seed=resolved_seed,
        modal_parts=modal_parts,
    )
    _trace_deterministic(result)
    try:
        validated = bundle.pydantic_cls.model_validate(result.data)
    except ValidationError as first_err:
        retry_prompt = _corrective_prompt(prompt, first_err)
        result = await backend.extract(
            prompt=retry_prompt,
            json_schema=json_schema,
            timeout=timeout,
            temperature=resolved_temperature,
            seed=resolved_seed,
            modal_parts=modal_parts,
        )
        _trace_deterministic(result)
        try:
            validated = bundle.pydantic_cls.model_validate(result.data)
        except ValidationError as second_err:
            raise ExtractionFailed(
                f"validation failed after retry for spec {name!r}"
            ) from second_err

    if not cache_disabled:
        _RESULT_CACHE[cache_key] = validated
    return validated


async def run_stream(
    name: str,
    text: str,
    *,
    backend: StreamingBackendAdapter,
    registry: Registry | None = None,
    timeout: float | None = None,
    temperature: float | _Unset = _UNSET,
    seed: int | None | _Unset = _UNSET,
    modal_parts: Sequence[ModalContent] = (),
    disable_cache: bool | _Unset = _UNSET,
) -> AsyncIterator[StreamEvent | BaseModel]:
    """Streaming sibling of :func:`run` (PLAN.md §2.4 G4).

    Yields each non-terminal ``StreamEvent`` (incremental deltas) as it arrives,
    then a single validated ``BaseModel`` as the final item. The adapter's
    terminal ``StreamEvent`` is consumed internally — its assembled payload runs
    through the *same* ``model_validate`` + single corrective re-extract as
    :func:`run`, and the validated instance is yielded in its place (the terminal
    event itself is never re-yielded). Validation parity is exact: a terminal
    payload that validates passes through; one that fails retries once with the
    corrective-feedback prompt (whose deltas also stream) then raises
    ``ExtractionFailed from second_err``.

    The result cache (Track H1) shares its key with :func:`run`, so the two paths
    are coherent for identical spec/backend/text/prompt. The terminal validated
    model is memoized only after the stream fully drains; a cache hit replays as
    an already-complete (non-streaming) result — the cached model is yielded as
    the sole item with no deltas and no adapter call. An aborted stream (consumer
    closes the generator before drain) and a run that fails validation both leave
    no cache entry, mirroring :func:`run`'s "never store a failed attempt" rule.
    """
    bundle = _resolve(registry).get(name)
    block = _read_prompiler_block()
    cache_disabled = _resolve_cache_disabled(disable_cache, block)

    cache_key = _result_cache_key(bundle.spec_hash, backend, text, modal_parts, bundle.prompt)
    if not cache_disabled:
        cached = _RESULT_CACHE.get(cache_key)
        if cached is not None:
            _RESULT_CACHE_STATS["hits"] += 1
            yield cached
            return
        _RESULT_CACHE_STATS["misses"] += 1

    _check_doc_size(text, bundle.max_input_tokens)

    resolved_temperature = _resolve_temperature(temperature, block)
    resolved_seed = _resolve_seed(seed, block)
    _warn_if_nondeterministic(backend)

    json_schema = bundle.pydantic_cls.model_json_schema()
    prompt = _assemble_prompt(bundle.prompt, text)

    terminal: ExtractResult | None = None

    async def _attempt(attempt_prompt: str) -> AsyncIterator[StreamEvent]:
        nonlocal terminal
        terminal = None
        async for event in backend.stream_extract(
            prompt=attempt_prompt,
            json_schema=json_schema,
            timeout=timeout,
            temperature=resolved_temperature,
            seed=resolved_seed,
            modal_parts=modal_parts,
        ):
            if event.is_terminal:
                terminal = event.result
            else:
                yield event

    async for event in _attempt(prompt):
        yield event
    captured = terminal
    if captured is None:
        raise ExtractionFailed(f"streaming adapter produced no terminal event for spec {name!r}")
    _trace_deterministic(captured)
    try:
        validated = bundle.pydantic_cls.model_validate(captured.data)
    except ValidationError as first_err:
        retry_prompt = _corrective_prompt(prompt, first_err)
        async for event in _attempt(retry_prompt):
            yield event
        captured = terminal
        if captured is None:
            raise ExtractionFailed(
                f"streaming adapter produced no terminal event for spec {name!r}"
            ) from first_err
        _trace_deterministic(captured)
        try:
            validated = bundle.pydantic_cls.model_validate(captured.data)
        except ValidationError as second_err:
            raise ExtractionFailed(
                f"validation failed after retry for spec {name!r}"
            ) from second_err
    if not cache_disabled:
        _RESULT_CACHE[cache_key] = validated
    yield validated


def run_sync(
    name: str,
    text: str,
    *,
    backend: BackendAdapter,
    registry: Registry | None = None,
    timeout: float | None = None,
    temperature: float | _Unset = _UNSET,
    seed: int | None | _Unset = _UNSET,
    modal_parts: Sequence[ModalContent] = (),
    disable_cache: bool | _Unset = _UNSET,
) -> BaseModel:
    """Sync wrapper over :func:`run` via :func:`asyncio.run`."""
    return asyncio.run(
        run(
            name,
            text,
            backend=backend,
            registry=registry,
            timeout=timeout,
            temperature=temperature,
            seed=seed,
            modal_parts=modal_parts,
            disable_cache=disable_cache,
        )
    )


async def run_batch(
    name: str,
    texts: Sequence[str],
    *,
    backend: BackendAdapter,
    registry: Registry | None = None,
    concurrency: int = _DEFAULT_BATCH_CONCURRENCY,
    timeout: float | None = None,
    temperature: float | _Unset = _UNSET,
    seed: int | None | _Unset = _UNSET,
    modal_parts: Sequence[ModalContent] = (),
    disable_cache: bool | _Unset = _UNSET,
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
                    temperature=temperature,
                    seed=seed,
                    modal_parts=modal_parts,
                    disable_cache=disable_cache,
                )
            except Exception as err:
                return err

    return await asyncio.gather(*(_run_one(t) for t in texts))
