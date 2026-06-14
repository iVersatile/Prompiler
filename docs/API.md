# Python API Reference

The public surface lives in four import roots: `prompiler` (compile + run),
`prompiler.spec` (load, lint, hash), `prompiler.runtime` (errors), and
`prompiler.backends` (adapters + credential providers). The streaming entry point
`run_stream` lives at `prompiler.runtime.orchestrator`. Everything else is internal
and may change without notice.

## `prompiler`

```python
from prompiler import (
    compile,
    run,
    run_sync,
    run_batch,
    ArtefactBundle,
    __version__,
    COMPILER_PROTOCOL_VERSION,
)
```

### `compile(spec) -> ArtefactBundle`

Pure, deterministic compilation of an `EntitySpec` into its derived artefacts. The
same spec and the same `prompiler` version always produce an identical bundle.
`compile` is an alias of `compile_spec`.

```python
from prompiler import compile
from prompiler.spec import load_spec

bundle = compile(load_spec("invoice.yaml"))
```

### `ArtefactBundle`

Frozen dataclass returned by `compile`.

| Field | Type | Description |
|-------|------|-------------|
| `prompt` | `str` | Assembled extraction prompt. |
| `pydantic_cls` | `type[BaseModel]` | Pydantic v2 model for the extracted fields. |
| `tool_schema_per_backend` | `Mapping[str, dict]` | Per-backend tool-call schema (read-only mapping). |
| `spec_hash` | `str` | Stable SHA-256 identity of the canonical spec. |
| `max_input_tokens` | `int \| None` | Optional input-size guard carried from the spec. |

`ArtefactBundle` has no `.spec` attribute — keep the `EntitySpec` from `load_spec`
if you need it alongside the bundle.

### `run(name, text, *, backend, registry=None, timeout=None, disable_cache=...) -> BaseModel`

Async. Resolves the compiled bundle for `name` from the registry, checks the
document against `max_input_tokens`, assembles the prompt, calls
`backend.extract(...)`, and validates the response against the Pydantic model. On a
validation error it issues **one** corrective-prompt retry; if that also fails it
raises `ExtractionFailed`.

### `run_sync(name, text, *, backend, registry=None, timeout=None, disable_cache=...) -> BaseModel`

Synchronous wrapper — `asyncio.run(run(...))`. Use it from non-async code.

### `run_batch(name, texts, *, backend, registry=None, concurrency=..., timeout=None, disable_cache=...) -> list[BaseModel | Exception]`

Async. Runs `run` across many documents with in-flight concurrency capped by an
`asyncio.Semaphore`. Per-item exceptions are **captured** into the result list, not
raised — a single bad document does not abort the batch. Results are positional:
index `i` in the output corresponds to `texts[i]`.

```python
results = await run_batch("invoice", documents, backend=my_backend)
for doc, result in zip(documents, results):
    if isinstance(result, Exception):
        log.warning("failed: %s", result)
    else:
        process(result)
```

### Cache opt-out

`run`, `run_sync`, `run_batch`, and `run_stream` share a process-local result
cache keyed by spec hash, backend, document, and prompt. The cache is **on** by
default. Disable it three ways, highest precedence first:

1. `disable_cache=True` — keyword argument on the call.
2. `PROMPILER_DISABLE_CACHE` — environment variable. `1` disables the cache; any
   other value forces it on. When the variable is set at all it overrides the
   `pyproject.toml` setting below.
3. `[tool.prompiler] disable_cache = true` — in `pyproject.toml`.

A disabled cache neither reads nor writes entries; every call hits the backend.

### Constants

- `__version__` — installed package version string.
- `COMPILER_PROTOCOL_VERSION` — bumped when the compilation contract changes; part of the spec-hash identity.

## `prompiler.spec`

```python
from prompiler.spec import (
    load_spec,
    lint_spec,
    spec_hash,
    canonical_yaml,
    EntitySpec,
    FieldSpec,
    Constraint,
    Label,
    LintIssue,
    SpecLoadError,
)
```

### `load_spec(path) -> EntitySpec`

Loads and parses a YAML spec from a `Path | str`. Raises `SpecLoadError` on malformed
YAML or schema violations.

### `lint_spec(spec) -> list[LintIssue]`

Returns lint findings (missing descriptions, duplicate field/label names, reserved
names). An empty list means the spec is clean. This is the check behind
`prompiler validate`.

### `spec_hash(spec) -> str`

Returns the canonical SHA-256 identity of the spec — the same value carried on
`ArtefactBundle.spec_hash`. Stable across reloads of equivalent specs.

### `canonical_yaml(spec) -> str`

Returns the canonical serialised form used as the hash input.

## `prompiler.runtime`

```python
from prompiler.runtime import (
    PrompilerError,      # base class
    SpecError,
    CompileError,
    AdapterError,
    ExtractionFailed,    # raised by run/run_sync after the retry fails
    EvalError,
    MCPError,
)
```

All runtime errors derive from `PrompilerError`, so a single `except PrompilerError`
catches everything the runtime raises. Catch `ExtractionFailed` specifically to
handle a document that could not be validated even after the corrective retry.

## `prompiler.backends`

```python
from prompiler.backends import (
    # adapter contracts
    BackendAdapter,
    StreamingBackendAdapter,
    StreamEvent,
    ModalContent,
    ExtractResult,
    CapabilityError,
    # concrete adapters
    ClaudeAdapter,
    OpenAIAdapter,
    GeminiAdapter,
    OllamaAdapter,
    # credential providers
    Credential,
    CredentialError,
    CredentialProvider,
    EnvVarProvider,
    GoogleADCProvider,
    KeychainProvider,
    OAuthProvider,
    # retry + observability
    RetryPolicy,
    with_retry,
    BackendCallMetrics,
    ObservabilityHook,
    PricingEntry,
    PricingTable,
    DEFAULT_PRICING_TABLE,
    emit_call_metrics,
)
```

### Adapter contracts

`BackendAdapter` is the `Protocol` every backend satisfies:
`extract(*, prompt, json_schema, timeout=None, temperature=0.0, seed=42, modal_parts=()) -> ExtractResult`,
plus `supports(feature)` and `to_tool_schema(json_schema)`. `ExtractResult` carries
the parsed `data` and usage metadata; `ModalContent` is one image/audio part passed
via `modal_parts`. `CapabilityError` is raised when a backend is handed a modality
it cannot serve (e.g. audio to a non-Gemini adapter). The shipped concrete adapters
are `ClaudeAdapter`, `OpenAIAdapter`, `GeminiAdapter`, and `OllamaAdapter`.

### Streaming types

`StreamingBackendAdapter` extends `BackendAdapter` with
`stream_extract(*, prompt, json_schema, timeout=None, temperature=0.0, seed=42, modal_parts=()) -> AsyncIterator[StreamEvent]`.
Only adapters that report `supports("streaming") is True` satisfy it.

`StreamEvent` is a frozen event in a stream — a sequence of *delta* events followed
by exactly one *terminal* event:

| Field | Type | Description |
|-------|------|-------------|
| `delta` | `str` | Raw output fragment (partial JSON text) on a non-terminal event. |
| `result` | `ExtractResult \| None` | `None` on a delta; the assembled `ExtractResult` on the terminal event. |
| `is_terminal` | `bool` (property) | `True` iff `result is not None`. |

The orchestrator-level entry point that drives a streaming adapter is
[`run_stream`](#streaming-prompilerruntimeorchestrator).

### Credential providers

A `CredentialProvider` resolves auth headers for a named backend
(`claude` / `openai` / `gemini`): `resolve(backend) -> Credential`. `Credential` is
a frozen `headers: dict[str, str]` bundle the adapter merges into its HTTP client, so
adapters stay oblivious to the *kind* of auth (vendor header vs bearer token). A
missing or invalid credential raises `CredentialError` — a single-line, actionable
message — at adapter **construction** time, before any HTTP traffic (fail-fast).

Four providers ship:

| Provider | Source | Notes |
|----------|--------|-------|
| `EnvVarProvider` | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | Default; emits the vendor header per backend. |
| `GoogleADCProvider` | Google Application Default Credentials | `gemini` only; needs the `adc` extra (`pip install 'prompiler[adc]'`). |
| `KeychainProvider` | OS keychain via `keyring` | Reads under service `prompiler`, keyed by backend; needs the `keychain` extra. |
| `OAuthProvider` | File-backed token store | Serves a cached bearer token, refreshes near expiry; primed by `prompiler login`. |

#### `KeychainProvider`

`resolve(backend)` reads the API key stored under the `prompiler` keychain service,
keyed by backend name, and returns the matching vendor auth header. Requires the
`keyring` library (`pip install 'prompiler[keychain]'`); a missing entry raises
`CredentialError`.

#### `OAuthProvider`

```python
OAuthProvider(store_path=None, client=None, now=None)
```

Serves a cached/refreshed bearer token from a file-backed token store (default
`$PROMPILER_OAUTH_STORE`, else `~/.config/prompiler/oauth_tokens.json`).
`resolve(backend)` is headless: it returns the cached `access_token` while valid,
performs a `refresh_token` grant when the token is within 60s of expiry, and raises a
"run `prompiler login <backend>`" `CredentialError` when the store is unprimed for
the backend. The interactive grant is primed out-of-band by the
[`prompiler login`](CLI.md#prompiler-login) command. `store_path` / `client` / `now`
are injectable for tests.

### Retry & observability

`with_retry(...)` wraps a coroutine in a bounded retry governed by `RetryPolicy`.
The observability hooks (`ObservabilityHook`, `BackendCallMetrics`,
`emit_call_metrics`) and pricing tables (`PricingEntry`, `PricingTable`,
`DEFAULT_PRICING_TABLE`) back the per-call usage metrics summarised by
`prompiler stats`.

## Streaming (`prompiler.runtime.orchestrator`)

```python
from prompiler.runtime.orchestrator import run_stream
```

### `run_stream(name, text, *, backend, registry=None, timeout=None, disable_cache=...) -> AsyncIterator[StreamEvent | BaseModel]`

Async generator — the streaming sibling of `run`. Requires a `backend` that
satisfies `StreamingBackendAdapter`. Yields each non-terminal `StreamEvent`
(incremental deltas) as it arrives, then a single validated `BaseModel` as the
**final** item. The adapter's terminal event is consumed internally: its assembled
payload runs through the *same* `model_validate` + single corrective re-extract as
`run`, and the validated model is yielded in the terminal event's place. A payload
that fails validation retries once (its deltas also stream) then raises
`ExtractionFailed`.

`run_stream` shares its result-cache key with `run` (see [Cache opt-out](#cache-opt-out)).
A cache hit replays as an already-complete result — the cached model is yielded as
the sole item, with no deltas and no adapter call. An aborted stream (the consumer
closes the generator before it drains) and a run that fails validation both leave no
cache entry.

```python
from prompiler.runtime.orchestrator import run_stream
from pydantic import BaseModel

async for item in run_stream("invoice", document, backend=streaming_backend):
    if isinstance(item, BaseModel):
        process(item)               # final, validated result
    else:
        print(item.delta, end="")   # incremental StreamEvent
```

## See also

- [Tutorial](TUTORIAL.md) — guided first run.
- [CLI reference](CLI.md) — command-line surface.
- [Architecture](architecture.md) — layer design and module map.
