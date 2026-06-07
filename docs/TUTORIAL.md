# Tutorial — Your First Spec in Under 15 Minutes

This walkthrough takes you from a cold `pip install` to a validated spec, generated
artefacts, and a structured extraction call. The target is a complete loop in under
fifteen minutes.

## 1. Install

```bash
pip install prompiler
prompiler --help
```

For development from source, use [uv](https://docs.astral.sh/uv/) instead:

```bash
uv sync
uv run prompiler --help
```

## 2. Write a spec

A spec is a single YAML file describing what to extract. Save the following as
`invoice.yaml`:

```yaml
spec_version: 1
name: invoice
task: extract
description: Extract billing details from a single invoice document.
fields:
  - name: vendor_name
    type: string
    required: true
    description: Legal name of the issuing vendor.
  - name: total_amount
    type: decimal
    required: true
    description: Grand total on the invoice in vendor currency.
```

Every field carries a `description`. The linter rejects specs with missing
descriptions, so this is not optional decoration — it is part of the contract.

## 3. Validate

```bash
prompiler validate invoice.yaml
```

`validate` loads the spec and runs the linter. Exit codes:

| Code | Meaning |
|------|---------|
| `0`  | Spec is valid and lint-clean |
| `1`  | Lint error (e.g. missing description, duplicate field) |
| `2`  | Path does not exist |

Wire this into [pre-commit](https://pre-commit.com/) to keep specs honest on every
commit — see the [README](../README.md#pre-commit-hook-downstream-projects).

## 4. Generate artefacts

```bash
prompiler codegen invoice.yaml -o .prompiler/compiled
```

This writes the deterministic artefacts derived from the spec (prompt, Pydantic
model, per-backend tool schema) into `.prompiler/compiled`. Generation is pure: the
same spec and the same `prompiler` version always produce byte-identical output.

## 5. Use it from Python

Load the spec, compile it, and inspect the bundle:

```python
from prompiler import compile
from prompiler.spec import load_spec

spec = load_spec("invoice.yaml")
bundle = compile(spec)

print(bundle.prompt)        # the assembled extraction prompt
print(bundle.spec_hash)     # stable SHA-256 identity of the spec
Model = bundle.pydantic_cls # Pydantic v2 model for the extracted fields
```

To run an actual extraction against a backend, use `run_sync`:

```python
from prompiler import run_sync
from prompiler.runtime import ExtractionFailed

document = "Invoice from Acme Corp. Total due: $1,240.00"

try:
    result = run_sync("invoice", document, backend=my_backend)
    print(result.vendor_name, result.total_amount)
except ExtractionFailed as exc:
    print("extraction failed after one corrective retry:", exc)
```

`run_sync` resolves the compiled bundle from the registry, calls the backend,
validates the response against the Pydantic model, and performs **one** corrective
retry on a validation error before raising `ExtractionFailed`.

## Next steps

- [API reference](API.md) — full Python surface (`compile`, `run`, `run_batch`, spec helpers).
- [CLI reference](CLI.md) — every command and flag.
- [Architecture](architecture.md) — how the layers fit together.
- [`examples/`](../examples/) — five worked specs covering arrays, enums, nested objects, patterns.
