# prompiler — Product Requirements Document

**Status:** v1 spec locked
**Date:** 2026-05-21
**License:** Apache 2.0
**Name validity:** `prompiler` confirmed available on PyPI, npm, and the GitHub user/org namespace as of 2026-05-21 (GitHub repo search returned zero hits). Fallback `xpiler` was rejected — npm package and GitHub user already taken. The product name is therefore locked as **`prompiler`**.

---

## 1. Summary

> **One spec. One prompt, schema, and tool. Four backends. Zero drift.**

`prompiler` is a config-first prompt compiler and schema synthesizer. Given a structured YAML spec describing an entity type (or classification target), it produces three coupled artefacts from a single source of truth:

1. A working LLM extraction (or classification) prompt.
2. A Pydantic v2 validation schema.
3. A registered, callable tool (Python function + tool-call schema for each supported backend + MCP tool).

The product is fully domain-agnostic. The same engine compiles specs for invoices, contracts, research citations, support tickets, clinical notes, or anything else.

### How it compares

| Concern                | Hand-rolled per project                                  | LangChain / Instructor                                          | **prompiler**                                                                          |
|------------------------|----------------------------------------------------------|------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| Schema source          | Duplicated: prompt + Pydantic + per-backend tool schema  | Pydantic class is source; prompt and tool grammar inferred per call | **Single YAML spec** compiles to prompt + Pydantic + 4 tool-call schemas                |
| Determinism            | Best-effort; left to caller                              | Best-effort; library passes temperature through                  | **Default temperature=0, seed=42**; compile is byte-identical for identical inputs      |
| Backend portability    | One adapter per vendor, hand-written                     | Vendor wrappers; behaviour leaks through abstractions            | **Claude, OpenAI, Gemini, Ollama** behind one adapter contract; cross-backend equivalence asserted in CI |
| Cassette testing       | Project-specific fixtures                                | Not provided                                                     | **Wire-level cassettes** redacted on write, replayed in integration tier               |
| MCP integration        | Custom per project                                       | Not provided                                                     | **Every registered spec auto-exposed** as an MCP tool (stdio + HTTP)                   |

### Getting started

```bash
pip install prompiler
prompiler init invoice --task extract
# edit specs/invoice.yaml — add fields, examples
prompiler compile invoice
prompiler run invoice --input ./samples/invoice.txt --backend claude
prompiler eval invoice --golden ./golden/invoice.jsonl
prompiler refine invoice --from-eval ./reports/invoice.json
prompiler serve --transport stdio   # exposes every spec as an MCP tool
```

Repository: `prompiler/`. (Historical fallback `xpiler` ruled out — see Name validity above.)

---

## 2. Problem Statement

Teams that extract structured data from unstructured text repeatedly hand-write three artefacts per entity type:

- A bespoke prompt with examples, edge cases, and output instructions.
- A schema (Pydantic, JSON Schema, or both) that validates the LLM response.
- A tool-call definition for each backend they target (Claude tool-use, OpenAI function-call, Gemini response_schema, Ollama, MCP).

These artefacts drift. Updating one without the others silently breaks production. Evaluation harnesses are home-grown per project. Backend differences (tool-call grammar quirks, type-coercion behaviour, refusal modes) leak into application code.

`prompiler` removes that drift by treating the entity spec as the only source of truth and generating every downstream artefact from it.

---

## 3. Goals

**v1 goals:**

- One-spec → prompt + Pydantic schema + tool callable across 4 backends (Claude, OpenAI, Gemini, Ollama).
- Two task types: `extract` and `classify`.
- Domain-agnostic: zero hardcoded vocabularies.
- Deterministic by default (temperature=0, seed=42).
- MCP server (stdio + HTTP) exposing every registered spec as an MCP tool.
- Closed-loop refinement: `prompiler eval` → JSON + HTML report → `prompiler refine` updates the prompt.
- Apache 2.0 license (explicit patent grant).
- Containerised runtime image (multi-arch, non-root, healthcheck) published alongside PyPI release.
- Containerised test pipeline (Ollama sidecar + uv test-runner, network-isolated for unit + integration tiers).

**v1 non-goals (deferred to v2+):**

- Streaming extraction (batch only in v1).
- Multi-modal input (text only in v1).
- Auto-refinement without human approval.
- Hash-keyed compile cache.
- Keychain / OAuth credential flows.
- Hosted UI.

---

## 4. Users & Use Cases

**Primary users:** Python backend engineers and ML engineers who:

- Need structured outputs from LLMs.
- Maintain more than three entity types.
- Switch backends or hedge across vendors.
- Want reproducible evaluation, not ad-hoc prompt tweaking.

**Use cases:**

| # | Use case | Task type |
|---|----------|-----------|
| 1 | Extract invoice line items, totals, tax from PDFs converted to text | extract |
| 2 | Classify inbound support emails into routing categories | classify |
| 3 | Extract citations + DOI + authors from research papers | extract |
| 4 | Pull contract obligations (party, action, deadline, penalty) | extract |
| 5 | Triage clinical free-text into ICD-10 candidate buckets | classify |
| 6 | Extract structured event timelines from incident reports | extract |

---

## 5. Core Concepts

### 5.1 EntitySpec

The single source of truth. Hand-authored YAML.

```yaml
spec_version: 1
name: invoice
task: extract
description: |
  Extract billing details from a single invoice document.
  Treat one invoice per call. Do not invent fields.
cite: true
fields:
  - name: vendor_name
    type: string
    required: true
    description: Legal name of the issuing vendor as printed on the invoice header.
  - name: invoice_number
    type: string
    required: true
    pattern: "^[A-Z0-9-]{3,32}$"
  - name: issue_date
    type: date
    required: true
  - name: total_amount
    type: decimal
    required: true
    description: Final amount due in invoice currency.
  - name: currency
    type: enum
    values: [USD, EUR, GBP, JPY, CHF]
    required: true
  - name: line_items
    type: array
    item:
      type: object
      fields:
        - { name: description, type: string, required: true }
        - { name: quantity, type: decimal, required: true }
        - { name: unit_price, type: decimal, required: true }
        - { name: line_total, type: decimal, required: true }
cross_field_constraints:
  - expr: "sum(line_items.line_total) == total_amount"
    severity: warn
```

Classification spec:

```yaml
spec_version: 1
name: email_category
task: classify
description: Route inbound support email into one routing bucket.
labels:
  - { name: billing,    description: Payments, invoices, refunds, subscription changes. }
  - { name: technical,  description: Product not working, bugs, performance, integration failures. }
  - { name: sales,      description: Pre-purchase questions, demo requests, pricing. }
  - { name: spam,       description: Unsolicited promotion, automated noise. }
allow_multi_label: false
```

### 5.2 Tasks (v1)

- `extract` — pull a structured object matching the spec from free text.
- `classify` — assign one (or more, if `allow_multi_label`) label to input text.

### 5.3 Artefacts produced per compile

| Artefact | File | Purpose |
|----------|------|---------|
| Prompt | `<name>.prompt.txt` | Human-readable LLM instructions + few-shot block. |
| Pydantic model | importable Python class | Strict source-of-truth validator. |
| Tool-call schema (per backend) | in-memory + `<name>.<backend>.toolspec.json` | Backend-specific projection of the schema. |
| Registered callable | `prompiler.registry.get("<name>")` | Async function wrapping select-backend → call → validate → return. |
| MCP tool descriptor | served from the MCP server | Same callable exposed over MCP. |

### 5.4 Registry

In-process registry maps spec name → compiled artefact bundle. Backed by:

- File-system discovery: scan `./prompts/*.yaml` on startup.
- Programmatic registration: `register_from_path()`, `register_from_dict()`.
- Hash check: `spec_hash` recomputed at load; collisions warn loudly.

### 5.5 Backend Adapters

Vendor-specific projection layer. Each adapter implements:

```python
class BackendAdapter(Protocol):
    name: str
    async def call(self, prompt: str, tool_schema: dict, input_text: str,
                   *, temperature: float, seed: int | None) -> RawResponse: ...
    def to_tool_schema(self, json_schema: dict) -> dict: ...
    def supports(self, feature: Literal["seed","tool_use","json_mode"]) -> bool: ...
```

v1 adapters: `claude`, `openai`, `gemini`, `ollama`.

### 5.6 Validation vs Tool-Call Schema

Pydantic model = strict source of truth. Tool-call schemas = per-backend lossy projection (some backends reject `pattern`, `format`, deep nesting, decimal types). Adapter is responsible for degradation. Output is always re-validated through Pydantic regardless of backend's claim of conformance.

---

## 6. Functional Requirements

### FR-1 — Spec Authoring

- Specs are YAML files matching the EntitySpec schema (`spec_version: 1`).
- `prompiler validate <path>` lints a spec without compiling.
- Linter checks: name uniqueness, reserved names, field-name collisions, unsupported types per backend, missing descriptions.
- Pre-commit hook target: `prompiler validate prompts/`.

### FR-2 — Compilation

- `prompiler compile <spec.yaml>` produces all artefacts under `./.prompiler/<name>/`.
- Compilation is deterministic: same spec + same `prompiler` version = byte-identical prompt + identical `spec_hash`.
- `spec_hash = sha256(canonical_yaml(spec) || prompiler_version)`.
- `prompiler compile --all` walks `prompts/` recursively.

### FR-3 — Extraction (callable surface)

Both async and sync surfaces:

```python
# async (primary)
result: Invoice = await prompiler.run("invoice", text, backend="claude")

# sync wrapper
result = prompiler.run_sync("invoice", text, backend="openai")
```

Behaviour:

- Selects adapter from `backend=` kwarg or env default.
- Calls adapter, receives raw JSON.
- Validates through Pydantic; on `ValidationError`, retries once with corrective feedback.
- Returns typed Pydantic instance or raises `ExtractionFailed`.

### FR-4 — Classification

Same surface, returns label (string) or list of labels.

### FR-5 — Batch Execution

```python
results = await prompiler.run_batch("invoice", texts, backend="claude", concurrency=8)
```

- Bounded concurrency with semaphore.
- Per-item error isolation; partial results returned with `.errors` collection.
- v1 is batch-only. Streaming deferred to v2.

### FR-6 — Eval Harness

- `prompiler eval <name> --fixture path/to/fixture.yaml --backend <b>` runs spec against fixtures.
- Fixture format (YAML):
  ```yaml
  - name: simple_acme_invoice
    input: |
      ACME Corp ... total $1,234.56 USD ...
    expected:
      vendor_name: ACME Corp
      total_amount: "1234.56"
      currency: USD
  ```
- Outputs:
  - `eval-report.json` — per-field precision/recall/F1, overall metrics, per-case diffs, `spec_hash`, backend, timestamp, token usage, cost estimate.
  - `eval-report.html` — interactive dashboard (zero JS framework, static HTML + minimal vanilla JS).

### FR-7 — Refinement Loop

- `prompiler refine <name> --report <eval-report.json>` proposes prompt patches.
- v1: human-in-the-loop. Diff shown; user confirms.
- v2: `--auto-apply` re-runs eval until metric threshold or N iterations.

### FR-8 — MCP Server

- `prompiler serve --transport stdio` or `--transport http --port 8765`.
- Every registered spec exposed as an MCP tool.
- Resources: `prompiler://specs/<name>` returns the spec; `prompiler://artefacts/<name>` returns generated prompt + tool schema.
- Token-usage reporting included in MCP tool response metadata.

### FR-9 — CLI Surface (v1)

```
prompiler validate <path|dir>
prompiler compile <spec.yaml> | --all
prompiler run <name> --input <file|-> [--backend <b>]
prompiler eval <name> --fixture <f> [--backend <b>]
prompiler refine <name> --report <r>
prompiler serve [--transport stdio|http] [--port N]
prompiler registry list
prompiler registry show <name>
```

### FR-10 — Credential Provider

- `CredentialProvider` protocol from day one.
- v1 implementations: `EnvVarProvider`, `GoogleADCProvider` (free auth for Gemini via `google-auth`).
- v2: `KeychainProvider`, `OAuthProvider`.
- Required env vars documented per backend; missing-key error is loud and points to docs.

### FR-11 — Configuration Surface

| Surface | Format | Location |
|---------|--------|----------|
| Entity specs | YAML | `prompts/*.yaml` |
| Eval fixtures | YAML | `tests/fixtures/*.yaml` |
| Project meta config | TOML | `pyproject.toml` under `[tool.prompiler]` |
| Compiled artefacts | JSON | `.prompiler/<name>/` |
| Eval reports | JSON + HTML | `.prompiler/eval/<name>/<timestamp>/` |

Project-level config example:

```toml
[tool.prompiler]
default_backend = "claude"
temperature = 0.0
seed = 42
prompts_dir = "prompts"
fixtures_dir = "tests/fixtures"
```

### FR-12 — Observability

- Structured logs (JSON Lines) per call: spec name, spec_hash, backend, model, latency, input_tokens, output_tokens, retries, validation outcome.
- Per-call cost estimate using vendor pricing table (shipped, updateable).
- `prompiler stats` summarises usage from local log file.
- OpenTelemetry hooks behind a flag (off by default).

### FR-13 — Doc-Size Handling

- Spec may declare `max_input_tokens`. Adapter rejects oversized input before any API call.
- v1: caller is responsible for chunking. `prompiler` ships a `chunk_for_extract()` utility but does not auto-chunk in `run()`.

### FR-14 — Determinism

- Defaults: `temperature=0`, `seed=42`.
- Adapters report whether seed is honoured (`supports("seed")`).
- v2: hash-keyed cache layer (`(spec_hash, backend, model, input_hash) → result`).

### FR-15 — Container Distribution

- Production runtime image published per release alongside the PyPI wheel.
  - Multi-stage `Dockerfile` (build stage with `uv` + final stage on `python:3.11-slim` or distroless).
  - Runs as a non-root UID; rootfs read-only where practical.
  - Multi-arch (`linux/amd64`, `linux/arm64`) via `docker buildx`.
  - Exposes MCP HTTP server (`prompiler serve --transport http`); bind defaults to `127.0.0.1` but a `--host 0.0.0.0` override is required inside containers and must be set explicitly by the operator.
  - HTTP `/healthz` endpoint returns 200 once registry load completes.
  - Cosign signature + SBOM attached to image.
- Test-time pipeline: `docker-compose.test.yml` brings up an Ollama sidecar (pinned model digest) and a uv-managed test-runner container. Unit + integration tiers run without external network egress.
- Image labels carry `org.opencontainers.image.{source,version,revision,licenses}` derived from the release tag.

---

## 7. Non-Functional Requirements

### 7.1 Performance

| Operation | Budget |
|-----------|--------|
| `compile` (single spec) | < 200 ms |
| `validate` (single spec) | < 50 ms |
| `run` (single call, excluding network) | < 50 ms overhead |
| `run_batch` 100 items, concurrency=8 (Ollama local) | < 60 s |
| MCP server cold start | < 1 s |

### 7.2 Reliability

- One retry on validation failure with corrective feedback.
- One retry on transient backend error (429, 5xx) with exponential backoff (1s, 2s, 4s; max 3 attempts total).
- Per-item isolation in batch mode.

### 7.3 Security

- No secrets in logs or error messages.
- No telemetry leaves the host by default.
- Spec files are treated as untrusted input by `validate` (no eval, no template substitution from unknown sources).
- MCP server binds `127.0.0.1` by default; explicit `--host 0.0.0.0` required to expose.

### 7.4 Compatibility

- Python 3.11+ (no 3.10).
- Pydantic v2 only.
- Async-first; sync wrappers via `asyncio.run`.

---

## 8. Out of Scope (v1)

- Streaming responses.
- Multi-modal (images, audio).
- Auto-applying refinement without confirmation.
- Hosted SaaS UI.
- Compile-result cache (deferred to v2).
- Keychain / OAuth credential flows.
- Spec composition / inheritance (single flat spec only in v1).

---

## 9. Success Metrics

| Metric | Target |
|--------|--------|
| Time from `cookiecutter`-style new spec → first green eval | < 15 min |
| Backend swap (single config change) preserves passing eval | 100% of v1 backends |
| Spec drift incidents (artefacts out of sync) | 0 (impossible by construction) |
| F1 uplift after one `refine` round on a baseline fixture | ≥ 5 pp median across canned demos |
| MCP tool invocation latency overhead vs direct call | < 20 ms p95 |

---

## 10. Risks & Open Questions

| Risk | Mitigation |
|------|------------|
| Vendor tool-schema drift (Claude/OpenAI/Gemini change grammar) | Adapter layer + nightly live-smoke catches drift fast. |
| Pydantic v2 → v3 migration churn | Pin major; track Pydantic changelogs in CI matrix. |
| Pricing-table rot for cost estimates | Versioned table, automated update PR via scheduled action. |
| Ollama model availability in CI | Pin model digests in docker-compose; vendor model into test image. |
| Eval fixture rot vs spec evolution | `spec_hash` on report; mismatched-hash fixtures flagged at run time. |

---

## 11. Glossary

| Term | Definition |
|------|------------|
| EntitySpec | YAML config describing one entity type or classification target. |
| Artefact | A file or object produced by `compile` (prompt, Pydantic model, tool schema, callable). |
| Registry | Runtime mapping of spec name → compiled artefact bundle. |
| Adapter | Backend-specific shim translating the canonical tool-call to vendor API. |
| Refinement | `eval → diff → patch prompt → re-eval` loop. |
| spec_hash | sha256 of canonical YAML serialisation + `prompiler` version. |
| Cassette | Recorded HTTP exchange replayed in tests (paid backends only). |
| ADC | Google Application Default Credentials (free auth path for Gemini). |

---

## 12. V2+ Items Under Review

> **Pending review — not part of the v1 contract.** Items in this section are candidates for re-evaluation post-v1 and may shift based on contributor signal, governance changes, or external constraints.

### 12.1 Code of Conduct — Custom vs. Verbatim CC 2.1

The v1 `CODE_OF_CONDUCT.md` is a customised adaptation of Contributor Covenant 2.1 (CC BY 4.0), not a verbatim copy. This section records the trade-off so a future reviewer can re-decide without re-deriving the analysis.

**Project impact**

| Dimension | Current state |
|-----------|---------------|
| Legal / IP | Apache-2.0 LICENSE untouched. CC BY 4.0 attribution requirement satisfied via the Attribution section in `CODE_OF_CONDUCT.md`. |
| GitHub Community Standards | Filename `CODE_OF_CONDUCT.md` detected by GitHub → repo profile completeness checkbox satisfied. |
| Enforcement surface | Custom 4-step ladder (no action / private warning / temporary restriction / permanent removal). Simpler than CC 2.1's 4-tier guidelines; stronger maintainer discretion clause; weaker written precedent. |
| Reporting channel | GitHub Security Advisory (private). No email PII collected. Appeal path = reply on the original advisory thread. |
| Contributor signal | Custom CoC reads as scannable and opinionated. Verbatim CC 2.1 would carry stronger external brand recognition. |
| Governance future | Swap to verbatim CC 2.1 is a one-file replace if a foundation or large external contributor base later requires it. |

**Product impact**

- **Direct:** zero. CoC governs human behaviour; it does not touch the runtime, compile pipeline, codegen, MCP server, or cassette layer.
- **Indirect:**
  - Procurement / enterprise-eval checkbox cleared (file exists and is detectable).
  - Triage flow routes conduct complaints to the advisory URL rather than maintainer inbox → no PII surface.

**Risk register**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Contributor dispute escalates beyond the 4-step ladder | LOW | Maintainer discretion clause + advisory appeal path. |
| "Unprofessional" perception from external eyes (foundation, sponsor, large contributor) | LOW | Attribution to CC 2.1 anchors legitimacy; swap cost is one file. |
| Foundation onboarding later requires verbatim CC 2.1 | LOW | Swap is a single-file replace; no downstream artefacts depend on the custom text. |
| Maintainer over-reach perception driven by discretion clause | MEDIUM | Soften discretion language when maintainer team grows beyond 3, or when first external contributor PR lands. |

**Recommendation**

Keep the custom CoC for v1. Re-evaluate when **either** trigger fires:

1. First external contributor PR is opened.
2. Maintainer team grows beyond 3 people.

At that point reassess against verbatim CC 2.1 with the risk register above as the baseline.
