# V2 Change Validation

This document explains how to tell, mechanically, whether a change to the
compiler (or to a scenario spec) altered the artefacts a downstream consumer
depends on. It is the safety net for the v1 → v2 transition: any drift in a
compiled **prompt**, **JSON schema**, or **`spec_hash`** is surfaced as a test
failure rather than silently shipped.

## The indicator: golden snapshots

Three runnable scenario clients live in [`examples/clients/`](../examples/clients/)
and consume the current v1 public API end to end:

| Scenario | Spec | Client |
|----------|------|--------|
| Invoice billing | [`examples/invoice.yaml`](../examples/invoice.yaml) | `invoice_client.py` |
| Clinical referral letter | [`examples/medical_letter.yaml`](../examples/medical_letter.yaml) | `medical_letter_client.py` |
| Point-of-sale receipt | [`examples/receipt.yaml`](../examples/receipt.yaml) | `receipt_client.py` |

For each scenario, `tests/test_e2e_clients.py` pins three artefacts under
`tests/golden/<scenario>.{prompt.txt,schema.json,hash}`:

- **`prompt.txt`** — the fully assembled extraction prompt.
- **`schema.json`** — the Pydantic v2 model JSON schema (sorted, indented).
- **`hash`** — the `spec_hash` (SHA-256 of canonical YAML + protocol version).

If a v2 change touches prompt assembly, schema generation, or hashing, at least
one golden diverges and the e2e suite fails. A green run is positive evidence
that consumer-visible output is byte-identical to the pinned baseline.

### Current pinned hashes

| Scenario | `spec_hash` |
|----------|-------------|
| invoice | `abf7aa547e1b13b6356721ffc920923f8638f9e4b5c3b558f78963ff8f712753` |
| medical_letter | `1c5d099f407d93eb22bb87435991a54850a3f96614f7c3df494db377fe3d1f3b` |
| receipt | `21418f35c43138331720b3193857f11862e0f3513cdf841aaa1d2423d7027deb` |

## Running the check

```bash
# Confirm artefacts still match the pinned baseline (CI default).
.venv/bin/pytest tests/test_e2e_clients.py -m e2e
```

The same suite also runs each client against a scripted backend to confirm the
public wiring (`register_from_path` + `run_sync` + one corrective retry) still
extracts the expected fields.

## Regenerating goldens (intentional changes only)

When a v2 change *intentionally* alters output, regenerate the baseline and
review the diff before committing:

```bash
PROMPILER_REGEN_GOLDEN=1 .venv/bin/pytest tests/test_e2e_clients.py -m e2e
git diff tests/golden/   # inspect every changed artefact deliberately
```

Never regenerate to "make the test pass" without understanding the diff — the
whole point of the indicator is that an unreviewed regeneration is a conscious
act, visible in the commit.

## Backends: scripted vs live

The clients default to a **scripted backend** (no API key, fully hermetic) so
the suite runs in CI and offline. To exercise a real model instead:

```bash
PROMPILER_LIVE=1 ANTHROPIC_API_KEY=... python examples/clients/invoice_client.py
```

The golden artefact checks are backend-independent — they validate compiler
output, not model responses — so they hold regardless of which backend runs.

## A note on the medical scenario

The clinical letter fixture is wholly synthetic and non-identifying. The patient
reference `PT-4471` is a pseudonymous code, not a real medical record number,
and no field maps to a real person. Keep it that way: never replace the fixture
with real PHI.

## See also

- [`TUTORIAL.md`](TUTORIAL.md) — first spec to first extraction.
- [`MANUAL_TESTING.md`](MANUAL_TESTING.md) — full local test pipeline by tier.
- [`architecture.md`](architecture.md) — how compilation produces these artefacts.
