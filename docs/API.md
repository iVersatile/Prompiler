# Python API Reference

The public surface lives in three import roots: `prompiler` (compile + run),
`prompiler.spec` (load, lint, hash), and `prompiler.runtime` (errors). Everything
else is internal and may change without notice.

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

### `run(name, text, *, backend, registry=None, timeout=None) -> BaseModel`

Async. Resolves the compiled bundle for `name` from the registry, checks the
document against `max_input_tokens`, assembles the prompt, calls
`backend.extract(...)`, and validates the response against the Pydantic model. On a
validation error it issues **one** corrective-prompt retry; if that also fails it
raises `ExtractionFailed`.

### `run_sync(name, text, *, backend, registry=None, timeout=None) -> BaseModel`

Synchronous wrapper — `asyncio.run(run(...))`. Use it from non-async code.

### `run_batch(name, texts, *, backend, registry=None, concurrency=..., timeout=None) -> list[BaseModel | Exception]`

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

## See also

- [Tutorial](TUTORIAL.md) — guided first run.
- [CLI reference](CLI.md) — command-line surface.
- [Architecture](architecture.md) — layer design and module map.
