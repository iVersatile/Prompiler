# prompiler — Architecture

**Status:** v1 architecture locked
**Date:** 2026-05-21
**Source of truth:** `PRD.md`, `PLAN.md`
**License:** Apache 2.0

---

## 1. Overview

`prompiler` is a layered Python library + CLI + MCP server. The single source of truth is the `EntitySpec` (YAML). All downstream artefacts (prompt, Pydantic model, per-backend tool-call schema, callable, MCP tool) are derived deterministically from that spec plus the `prompiler` version.

### 1.1 Layered View

```
┌─────────────────────────────────────────────────────────────────────┐
│ Consumers                                                           │
│   Python app │ CLI │ MCP client (Claude Desktop, Inspector, others) │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────┐
│ Public API                                                          │
│   prompiler.compile()  prompiler.run()  prompiler.run_batch()       │
│   prompiler.eval()     prompiler.refine()                           │
└─────┬───────────────────────┬─────────────────────────────┬─────────┘
      │                       │                             │
      ▼                       ▼                             ▼
┌──────────┐         ┌────────────────┐            ┌────────────────┐
│ Compiler │         │ Runtime        │            │ Eval + Refine  │
│  spec →  │         │  registry  →   │            │  fixtures →    │
│  prompt  │         │  adapter call  │            │  metrics →     │
│  model   │         │  validation    │            │  diff → patch  │
│  schema  │         │  retry         │            │                │
└────┬─────┘         └────────┬───────┘            └────────┬───────┘
     │                        │                             │
     ▼                        ▼                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Registry │ Backend Adapters │ Reporters │ MCP Server │ CLI          │
│          │ claude / openai /│ JSON+HTML │ stdio/HTTP │ typer        │
│          │ gemini / ollama  │           │            │              │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────┐
│ I/O Boundary                                                        │
│   File system │ HTTP (vendor APIs) │ JSONL logs │ Credential store  │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Module Layout

```text
src/prompiler/
├── __init__.py              # public API surface
├── spec/
│   ├── loader.py            # YAML safe_load + EntitySpec model
│   ├── model.py             # Pydantic model of the spec format itself
│   ├── hash.py              # canonical YAML + spec_hash
│   └── linter.py            # validate-only checks
├── compiler/
│   ├── pydantic_synth.py    # spec → pydantic.create_model
│   ├── prompt_synth.py      # spec → prompt text
│   ├── jsonschema_emit.py   # pydantic model → JSON Schema (2020-12)
│   └── constraints.py       # cross-field constraint compiler (restricted DSL)
├── adapters/
│   ├── base.py              # BackendAdapter protocol
│   ├── claude.py            # Anthropic tool-use
│   ├── openai.py            # function-call / response_format
│   ├── gemini.py            # response_schema (ADC-friendly)
│   ├── ollama.py            # local JSON-mode
│   ├── degrade.py           # per-backend JSON Schema projection rules
│   └── credentials.py       # CredentialProvider protocol + impls
├── runtime/
│   ├── registry.py          # in-process spec name → artefact bundle
│   ├── orchestrator.py      # run / run_sync / run_batch
│   ├── retry.py             # validation + transient retry policies
│   ├── errors.py            # PrompilerError hierarchy
│   └── chunk.py             # chunk_for_extract utility
├── eval/
│   ├── fixtures.py          # YAML fixture loader
│   ├── runner.py            # eval loop
│   ├── metrics.py           # per-field precision / recall / F1
│   ├── report_json.py       # eval-report.json emitter
│   └── report_html.py       # static HTML dashboard
├── refine/
│   ├── tutor.py             # patch-proposing LLM call
│   ├── differ.py            # unified-diff preview + apply
│   └── reeval.py            # re-run eval + delta surface
├── mcp/
│   ├── server.py            # mcp SDK wiring
│   ├── tools.py             # spec → MCP tool descriptor
│   ├── resources.py         # prompiler://specs/* and ://artefacts/*
│   └── transports.py        # stdio + HTTP (127.0.0.1 default bind)
├── cli/
│   ├── main.py              # typer app root
│   ├── validate.py
│   ├── compile.py
│   ├── run.py
│   ├── eval.py
│   ├── refine.py
│   ├── serve.py
│   ├── registry.py
│   └── stats.py
├── observability/
│   ├── logging.py           # JSONL log writer
│   ├── otel.py              # OpenTelemetry hooks (off by default)
│   └── cost.py              # per-call cost estimate from pricing table
└── pricing/
    ├── v1.json              # shipped pricing table (updateable)
    └── loader.py            # pricing table loader + valid_until check
```

### 1.3 Public API Boundary

```python
from prompiler import compile, run, run_sync, run_batch, eval, refine
from prompiler.registry import register_from_path, register_from_dict, get
from prompiler.errors import (
    PrompilerError, SpecError, CompileError, AdapterError,
    ExtractionFailed, EvalError, MCPError,
)
```

Everything under `prompiler._internal.*` is private. Module-level `__all__` enforced on every public submodule. Semantic versioning applies to the public surface only.

### 1.4 Data Flow — Single Extract Call

```
caller ─► run("invoice", text, backend="claude")
            │
            ▼
   registry.get("invoice") ── ArtefactBundle{prompt, pydantic_cls,
                                              tool_schema_per_backend,
                                              spec_hash}
            │
            ▼
   orchestrator.run()
            │
            ├─► adapter = adapters.get("claude")
            │   adapter.call(prompt, tool_schema, text,
            │                temperature=0, seed=42)
            │
            ▼
   raw JSON ── pydantic_cls.model_validate(raw)
            │     │
            │     ▼ ValidationError? retry once with corrective feedback
            │
            ▼
   typed Invoice instance ─► caller
            │
            └─► observability.log(JSONL line:
                  ts, spec, spec_hash, backend, model,
                  latency_ms, input_tokens, output_tokens,
                  cost_estimate_usd, retries, outcome)
```

### 1.5 Concurrency Model

- **Async-first.** All adapter calls, MCP transport, and orchestration are `async def`. Sync surface is a thin `asyncio.run` wrapper around the async one.
- **Batch concurrency** bounded by `asyncio.Semaphore(concurrency)` inside `run_batch`. Default `concurrency=8`. Per-item isolation: one item's exception never aborts the batch.
- **Registry is read-mostly.** Built once at startup (or via explicit `register_*` calls); reads are lock-free. Mutations during runtime use a single `asyncio.Lock` — discouraged in production hot paths.
- **No global mutable state** in compiled artefacts. Pydantic model classes are pure; tool schemas are dataclasses frozen at compile time.

### 1.6 Determinism Contract

**Determinism is adapter-conditional, not universal.** Compile-time determinism is unconditional. Run-time determinism depends entirely on whether the chosen backend honours `seed`.

- **Compile-time (unconditional).** Inputs to `compile`: spec YAML + `prompiler` version → byte-identical artefacts. Verified by the local test gate (`docs/RULES.md` §9.3): each example spec is compiled twice and the two artefacts must `diff -q` clean.
- **Run-time (conditional on adapter capability).** Inputs to `run`: spec + adapter + model + input text + `temperature=0` + `seed=42` → identical output **iff** the adapter reports `supports("seed") == True`. Adapters that do not honour seed mark the trace `deterministic=false` and the orchestrator emits a one-shot WARN log per process for that backend.
- **`spec_hash`** is recomputed on load and stamped on every artefact and every eval report.

#### Adapter seed-support matrix

| Adapter | `supports("seed")` | Run-time determinism | Notes |
|---------|--------------------|----------------------|-------|
| `adapter-ollama` | `True` | Reproducible across runs on the same model digest at `temperature=0`, `seed=42`. | Model is pinned by digest in `docker-compose.test.yml`; this is the gold standard for run-time determinism in tests. |
| `adapter-openai` | `True` (best-effort) | OpenAI accepts `seed` and returns a `system_fingerprint`; outputs are reproducible **only when `system_fingerprint` is unchanged between runs**. Fingerprint drift on model rollouts re-baselines determinism. | The runtime records `system_fingerprint` on every trace; cassette diffs surface drift early. |
| `adapter-claude` | `False` | Not reproducible. Anthropic's Messages API exposes no seed parameter at the time of writing. | Traces are tagged `deterministic=false`. Evaluation suites must use cassette playback rather than live calls when assertion-on-output equality is required. |
| `adapter-gemini` | `False` | Not reproducible across runs. Gemini's public API does not expose a seed parameter. | Same posture as Claude — `deterministic=false`, cassette playback for output-equality assertions. |

The two `False` rows are deliberate. Determinism is a property of the backend, not of `prompiler`; the architecture's job is to surface that property honestly in the trace, not to fabricate it.

---

## 2. Component Detail

### 2.1 Spec Layer (`spec/`)

- **Loader.** `yaml.safe_load` only. Unsafe loaders (`Loader=yaml.Loader`, `yaml.load`) are banned by a lint rule and a unit test that greps the source tree.
- **Spec model.** `EntitySpec` is itself a Pydantic v2 model. Field types restricted to: `string`, `integer`, `decimal`, `boolean`, `date`, `datetime`, `enum`, `array`, `object`, `optional` modifier.
- **Hash.** `spec_hash = sha256(canonical_yaml(spec) || prompiler_version)`. Canonicalisation: keys sorted, list order preserved, integers normalised, strings UTF-8 NFC.
- **Linter.** Pure-function checks. Detects: duplicate field names, reserved names, missing descriptions, unsupported types per declared backend target, contradictory `required`/`default`, suspicious regex patterns (catastrophic backtracking heuristic).

### 2.2 Compiler (`compiler/`)

- **Pydantic synthesiser.** Uses `pydantic.create_model`. `decimal` → `Decimal`. `date`/`datetime` → `date`/`datetime` with strict parsing. `enum` → dynamically generated `StrEnum`. `array<object>` → nested generated model.
- **Prompt synthesiser.** Template-driven (Jinja2 with `autoescape=True`). Sections: role framing, task description, field-by-field instruction block, citation directive (if `cite: true`), few-shot examples, output schema reminder. Template inputs are bounded — no spec-controlled template injection.
- **JSON Schema emitter.** Pydantic v2's native `model_json_schema()` with explicit draft 2020-12 selection. Output passes a JSON-Schema-meta validator in unit tests.
- **Constraint compiler.** Cross-field constraints (`sum(line_items.line_total) == total_amount`) parsed via a **restricted expression DSL** (built on `ast.parse` with a whitelist of node types: `BinOp`, `Compare`, `Call(name in {sum,len,abs,min,max})`, `Attribute`, `Subscript`, `Name`, `Num`). **No `eval`, no `exec`, no arbitrary attribute access.** Out-of-whitelist node raises `SpecError` at compile time.

### 2.3 Adapters (`adapters/`)

- **Protocol** (verbatim from `PRD.md` §5.5):
  ```python
  class BackendAdapter(Protocol):
      name: str
      async def call(self, prompt: str, tool_schema: dict, input_text: str,
                     *, temperature: float, seed: int | None) -> RawResponse: ...
      def to_tool_schema(self, json_schema: dict) -> dict: ...
      def supports(self, feature: Literal["seed","tool_use","json_mode"]) -> bool: ...
  ```
- **Degradation rules** live in `degrade.py`. Per-backend table of unsupported JSON Schema keywords (`pattern`, `format`, `minLength`, depth caps, `additionalProperties`, decimal handling) with explicit drop-or-translate policy. Every degradation is logged at debug level.
- **CredentialProvider** abstraction:
  - `EnvVarProvider` (default): reads env vars per backend (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).
  - `GoogleADCProvider`: uses `google-auth` ADC chain (free for Gemini in dev).
  - v2: `KeychainProvider`, `OAuthProvider` (out of scope here).
- **Retry policy.** Transient (HTTP 429, 5xx, connection errors) → exponential backoff `1s, 2s, 4s`, max 3 attempts. Validation failure → 1 corrective retry with a "your previous output failed these constraints" feedback block. Total retry budget capped to prevent runaway cost.

### 2.3a Cassette Redaction Algorithm

Cassettes are the project's primary integration-test substrate (see `docs/MANUAL_TESTING.md` §5). They capture wire-level HTTP requests and responses to vendor endpoints and must be free of credentials before commit. Redaction is **mandatory on write**, **defensive on read**, and **enforced by a pre-commit hook** (`docs/RULES.md` §8, last bullet).

#### Three-layer pipeline (write path)

Every recorded interaction passes through three layers before being serialised to YAML on disk:

1. **Header allow-list.** Strip every request and response header by default; emit only an explicit allow-list. The current allow-list is:
   ```
   content-type, content-length, user-agent, x-request-id,
   anthropic-version, openai-version, x-goog-api-client,
   x-ratelimit-*, retry-after, date
   ```
   Anything outside the list — `authorization`, `x-api-key`, `cookie`, `set-cookie`, `proxy-authorization`, any `*-token` or `*-key` header — is dropped, not masked. Dropping > masking: a masked header still discloses key presence and naming.
2. **Body JSON path scrub.** For request and response bodies with `content-type: application/json`, walk the parsed JSON and replace values at known sensitive paths with `"<REDACTED>"`. The path set is adapter-scoped and lives in `tests/cassettes/redactors.py`:
   - **Claude:** `$.metadata.user_id`, `$.system[?(@.type=='ephemeral_key')]`
   - **OpenAI:** `$.user`, `$.metadata.user_id`
   - **Gemini:** `$.contents[*].parts[*].inline_data` (binary payloads are dropped to a `<REDACTED-BINARY-N-bytes>` placeholder, never re-encoded)
   - **All adapters:** any value whose key matches `(?i)(api[_-]?key|secret|token|password|bearer|authorization)`
3. **Regex fallback over the serialised body.** After JSON scrubbing (or for non-JSON bodies), run a regex sweep across the rendered string for high-confidence patterns:
   - `sk-ant-[A-Za-z0-9_-]{20,}` (Anthropic API keys)
   - `sk-[A-Za-z0-9]{20,}` (OpenAI API keys, legacy + project-scoped)
   - `ya29\.[A-Za-z0-9_-]+` (Google OAuth access tokens)
   - `eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}` (JWTs)
   - `AKIA[0-9A-Z]{16}`, `ASIA[0-9A-Z]{16}` (AWS access keys, in case of proxied requests)

   Matches are replaced with `<REDACTED>`. The regex set is the same one used by `scripts/scan_secrets.py` — a single source of truth ensures the pre-commit hook and the cassette recorder never disagree.

#### Read path

Cassette load in **strict playback** (the default) does not re-scrub; it asserts that no redaction pattern appears in the on-disk fixture. If one does — for example, because a developer hand-edited a cassette — the integration test fails loudly with the offending path and the matched pattern. This catches regressions in the write path before they reach CI.

#### Refresh cadence

- **Quarterly.** A scheduled CI job re-records every cassette against a sandbox tenant with synthetic data and posts the diff to the team for review. Drift in vendor wire formats surfaces here before it breaks a release.
- **On adapter change.** Any PR that touches `src/prompiler/adapters/<vendor>.py` must include re-recorded cassettes for that vendor, or document why playback is unaffected. The reviewer checklist (`docs/RULES.md` §6) enforces this.
- **On vendor-announced breaking change.** Out-of-band refresh, tracked in `docs/LESSONS_LEARNT.md` with tag `cassettes` and the affected adapter tag.

#### Non-goals

- **No PII scrubbing.** Cassettes use synthetic input documents from `examples/`; real customer data must never be recorded in the first place. The redactor does not attempt to detect PII in free-text bodies.
- **No automatic re-recording in CI on failure.** A cassette-mismatch failure is a signal, not a problem to paper over. Re-records are explicit human actions (`PROMPILER_CASSETTE_MODE=record`) reviewed in a PR.

### 2.4 Runtime (`runtime/`)

- **Registry.** In-process dict `name → ArtefactBundle`. File-system discovery scans `prompts/` on startup (configurable via `pyproject.toml` `[tool.prompiler]`). Programmatic registration via `register_from_path()` and `register_from_dict()`. Hash collision warns; duplicate name raises.
- **Orchestrator.** Implements `run`, `run_sync`, `run_batch`. Pipeline: registry lookup → adapter resolve → call → JSON parse → Pydantic validate → retry-once → return typed instance.
- **Doc-size guardrail.** `max_input_tokens` enforced before any API call. Adapter-specific tokenisers used where available; fallback to a conservative character-count heuristic.
- **Error hierarchy.**
  ```
  PrompilerError
  ├── SpecError           # bad spec at parse/lint time
  ├── CompileError        # bad spec at compile time
  ├── AdapterError        # vendor API problem (network, auth, refusal)
  ├── ExtractionFailed    # validation failure after retry budget
  ├── EvalError           # fixture parse / metric computation
  └── MCPError            # MCP transport problem
  ```

### 2.5 Eval (`eval/`)

- Fixture loader expects YAML. Each case: `name`, `input` (string), `expected` (dict matching spec shape).
- Runner executes fixtures via the same `runtime.run` as production. **No fast path** — eval exercises the real pipeline.
- Metrics: per-field precision / recall / F1 over scalar fields; for arrays-of-objects, set-based match on key field (declared in fixture metadata).
- **`eval-report.json`** schema:
  ```jsonc
  {
    "spec": "invoice",
    "spec_hash": "sha256:...",
    "backend": "claude",
    "model": "claude-...",
    "timestamp": "2026-05-21T01:38:00Z",
    "fixture_path": "tests/fixtures/invoice.yaml",
    "aggregate": { "precision": 0.93, "recall": 0.91, "f1": 0.92 },
    "per_field":  { "vendor_name": { "p": 1.0, "r": 1.0, "f1": 1.0 }, ... },
    "per_case":   [ { "name": "...", "diff": [...], "ok": true }, ... ],
    "usage":      { "input_tokens": 12034, "output_tokens": 988,
                    "cost_estimate_usd": 0.042 }
  }
  ```
- **HTML report.** Static file. Zero JS framework. One vanilla JS file (≤ 5 kB) for table sort/filter. Total gzipped budget < 200 kB. Lighthouse a11y ≥ 95. Verified at 320 / 768 / 1440.

### 2.6 Refine (`refine/`)

- Tutor: LLM call with the eval report + current prompt + a fixed system prompt asking for a unified diff over the prompt text.
- Diff applier: parses unified diff, shows preview to user, requires explicit confirmation. v1 never auto-applies. v2 adds `--auto-apply` with metric-threshold and iteration cap.
- Re-eval after apply: surface metric delta vs previous report.
- Refusal handling: if tutor declines or returns a malformed diff, surface `RefineError` and exit non-zero. No silent fallback.

### 2.7 MCP Server (`mcp/`)

- Implementation built on the official `mcp` Python SDK.
- One MCP tool per registered spec; input schema = adapter-agnostic projection; output schema = pydantic-derived JSON Schema.
- Resources:
  - `prompiler://specs/<name>` — raw spec YAML.
  - `prompiler://artefacts/<name>` — bundle of prompt text + tool schema.
- Transports: stdio (default), HTTP. HTTP binds `127.0.0.1` by default; `--host 0.0.0.0` is opt-in and emits a loud warning.
- Tool response metadata always includes token usage and cost estimate.

### 2.8 CLI (`cli/`)

- Typer-based. Subcommands listed in `PRD.md` §FR-9.
- Exit codes: `0` success, `1` user error (bad spec, bad args), `2` internal error.
- All commands respect a global `--quiet` / `--verbose` pair and `--no-color`.
- Pre-commit hook target: `prompiler validate prompts/`.

### 2.9 Observability (`observability/`)

- **JSONL log writer.** One line per call. Fields: `ts`, `spec`, `spec_hash`, `backend`, `model`, `latency_ms`, `input_tokens`, `output_tokens`, `cost_estimate_usd`, `retries`, `outcome`. Path: `${XDG_STATE_HOME:-~/.local/state}/prompiler/calls.jsonl`.
- **OpenTelemetry hooks.** OFF by default. Behind `--telemetry` flag and `[tool.prompiler] telemetry = true`. No telemetry leaves the host without explicit opt-in.
- **Cost.** `pricing/v1.json` shipped with the package. `valid_until` field; if expired, `prompiler stats` and per-call logs warn (never hard-fail). Update path: scheduled action opens PR with refreshed table.

---

## 3. Testing Strategy

### 3.1 Tiered Pipeline

| Tier | Scope | When | Backends | Budget |
|------|-------|------|----------|--------|
| unit | pure functions, compiler, schema synth, prompt synth, linter, hash, degradation rules, metrics | every push | mocks only | < 30 s |
| integration | adapter contracts, runtime orchestration, registry, refine differ | every push | Ollama sidecar + cassettes | < 2 min |
| e2e | CLI subprocess flows, MCP stdio + HTTP, refinement end-to-end | every PR | Ollama + cassettes | < 5 min |
| live-smoke | drift detection against real vendor APIs | nightly + manual | real Claude / OpenAI / Gemini | < 10 min |

Live-smoke is **never blocking on PRs**. Vendor flakiness must not gate merges. A failure files an issue (and pages on-call in production deployments).

### 3.2 Containerised Test Pipeline

`docker-compose.test.yml` provides:

- **Ollama sidecar** with a pinned model digest baked into the test image cache. Used by integration + e2e tiers.
- **Test runner container** with `uv`-managed deps frozen by `uv.lock`.
- Shared network; no external egress for unit + integration tiers (enforced via container network config).

GitHub Actions matrix runs each tier as a separate job. Failure in lower tiers short-circuits higher tiers per PR.

### 3.3 Cassette Policy (VCR-Style)

- Paid backends (Claude, OpenAI, Gemini) recorded into JSON cassettes once, then replayed deterministically.
- Recording mode is opt-in via env var (`PROMPILER_CASSETTE_MODE=record`). Default is **strict playback**: a missing cassette fails the test, never silently records.
- Redaction-on-write: Authorization headers, API keys, account IDs scrubbed before commit.
- Cassette refresh checklist mandatory in PR template when adapter code changes.

### 3.4 Determinism Tests

- Compile twice; assert byte-identical artefact bundle.
- Eval twice on Ollama with `temperature=0, seed=42`; assert identical typed output for seed-supporting models.
- `spec_hash` stamped on every artefact and every eval report; mismatch surfaces a warning at run-time.

### 3.5 Performance Assertions in CI

Every NFR budget from `PRD.md` §7.1 is asserted by a CI test:

- `compile` single spec < 200 ms.
- `validate` single spec < 50 ms.
- `run` overhead (excl. network) < 50 ms.
- `run_batch` 100×Ollama with concurrency=8 < 60 s.
- MCP cold start < 1 s.
- MCP overhead vs direct call < 20 ms p95.

### 3.6 Coverage Targets

- Compiler ≥ 90 % (P1 DoD).
- Adapters ≥ 85 % per module (P2 DoD).
- Runtime ≥ 85 % (P3 DoD).
- Eval ≥ 85 % (P4 DoD).
- Refine ≥ 80 % (P5 DoD).
- Whole-product floor: ≥ 80 % (matches global rule).

### 3.7 Static Analysis

- `mypy --strict` clean — no `Any` leakage in public surface.
- `ruff` + `black` enforced via pre-commit.
- Custom lint: regex check that bans `yaml.load`, bans `eval`/`exec` outside the constraint DSL parser, bans `0.0.0.0` literal outside the MCP serve flag plumbing.

---

## 4. Security Risks & Mitigations

| # | Risk | Mitigation |
|---|------|------------|
| S1 | **Untrusted YAML execution.** A malicious spec uses unsafe YAML tags to instantiate arbitrary classes. | `yaml.safe_load` only. Banned-import lint. Unit test greps source for `yaml.load(` and fails build. |
| S2 | **Cross-field constraint DSL turns into RCE.** Users embed Python via `eval` paths. | Restricted DSL with `ast.parse` whitelist (BinOp, Compare, Name, Subscript, Attribute, Call to `{sum,len,abs,min,max}`). No `eval` / `exec` / `__import__`. Out-of-whitelist nodes raise `SpecError`. |
| S3 | **Prompt template injection** from spec fields. | Jinja2 with `autoescape=True`; specs are typed inputs to the template, never themselves Jinja sources. Few-shot examples scanned for instruction-override strings. |
| S4 | **MCP server bound to public interface by accident.** | Default bind `127.0.0.1`. `--host` argument validated; binding to non-loopback emits a loud warning and requires explicit `--allow-public`. |
| S5 | **MCP path traversal** in `prompiler://artefacts/<name>`. | Resource name must match `^[a-z0-9_-]+$`; lookup goes through the registry, never the file system. |
| S6 | **Credentials in logs / errors / reports.** | `EnvVarProvider` strips values from any logging surface. Cassette redaction list covers `Authorization`, `x-api-key`, `OAuth`, `Bearer` patterns. Eval-report emitter has a unit test that asserts no env-var values leak. |
| S7 | **Supply-chain compromise.** | OIDC PyPI publish. SBOM published with each GitHub release. Pinned versions in `uv.lock`. Weekly dependency audit. Dependabot for security advisories. |
| S8 | **Pricing table tampered with to hide cost.** | Pricing table file shipped inside the wheel; `valid_until` checked at load; reload from disk allowed only via explicit `prompiler config refresh-pricing` (not auto-fetched from the network). |
| S9 | **Vendor API responses with prompt-injection payloads** leaking into downstream user-facing surfaces. | Eval HTML report HTML-escapes every model output value. CLI `run` prints structured output, not raw model text. |
| S10 | **Refusal-mode information leak.** Adapter surfaces vendor refusal verbatim into logs. | Refusals classified into an enum (`refusal_policy`, `refusal_safety`, `refusal_other`); raw vendor message logged only at debug level, never warn/error. |
| S11 | **OpenTelemetry export leaks PII.** | OTel off by default. When on, span attributes are an explicit allow-list (no input text, no output text — counts and IDs only). |

---

## 5. Performance Risks & Mitigations

| # | Risk | Mitigation |
|---|------|------------|
| P1 | **Compile cost on large specs** exceeds 200 ms budget. | In-process `spec_hash → ArtefactBundle` cache. Pydantic model creation is the dominant cost; benchmark and cap field count in linter advisory. |
| P2 | **Batch concurrency starves the event loop** on slow backends. | `asyncio.Semaphore` bounds concurrency. Per-call timeout (vendor-dependent default). Backpressure surfaced as `BatchTimeoutError` for that item only. |
| P3 | **MCP overhead exceeds 20 ms p95.** | Tool dispatch is a dict lookup; resource handlers are pure. Benchmark in CI; regression > 5 ms fails the build. |
| P4 | **Eval report bloat** (HTML > 200 kB for large fixtures). | Per-case diffs truncated past a configurable cap; full diffs available in the sibling JSON. Hard size budget asserted in CI. |
| P5 | **Validation retry storm.** A persistently bad model floods budget. | Retry capped at **1** validation retry per call. Adapter-level transient retry capped at 3 attempts with backoff. Combined budget surfaced in metrics. |
| P6 | **Cassette set grows unbounded.** | Naming convention scopes cassettes per test; CI lints orphaned cassettes (not referenced by any test) and fails the build. |
| P7 | **Schema translation cost** (Pydantic JSON Schema → backend tool schema) per call. | Computed once at compile time; stored in `ArtefactBundle`. Per-call cost is `O(1)` lookup. |
| P8 | **Pricing table rot** producing wildly wrong cost estimates. | `valid_until` warning surfaced in `prompiler stats` and per-call logs. Never hard-fail on stale pricing. |
| P9 | **Registry startup cost** scanning huge `prompts/` directories. | Discovery is incremental; `compile` is lazy per-spec on first access; full preload only on explicit `prompiler registry list --warm`. |
| P10 | **JSONL log file growth.** | Rotation policy: 100 MB per file, 5-file ring. Documented `prompiler stats --truncate` for explicit pruning. |

---

## 6. Cross-Cutting Concerns

### 6.1 Configuration Precedence

Highest wins:

1. Explicit function kwargs / CLI flags
2. Environment variables (`PROMPILER_*`)
3. `pyproject.toml` `[tool.prompiler]`
4. Built-in defaults

Resolution is centralised in `runtime/config.py`. No per-module reads of env vars.

### 6.2 Config-Format-Per-Surface

| Surface | Format |
|---------|--------|
| Entity specs, eval fixtures | YAML (human-authored) |
| Project meta config | TOML in `pyproject.toml` |
| Compiled artefacts, eval reports, pricing table | JSON (machine artefacts) |

### 6.3 Logging

- One `logging.Logger` per top-level module.
- Levels: `DEBUG` for adapter-level wire detail and degradation traces; `INFO` for compile / run / eval lifecycle events; `WARN` for degraded behaviour or stale pricing; `ERROR` only on failures surfaced to the caller.
- Structured JSONL log writer is separate from human-readable stderr logger.

### 6.4 Versioning

- Semantic versioning on the public API surface (`prompiler.*`, not `_internal.*`).
- `prompiler.version` exposed and stamped into every `spec_hash`.
- Spec format carries its own `spec_version: 1`; bumping the spec format is a major version event for `prompiler`.

### 6.5 Docs Surface

- Tutorial (invoice walkthrough).
- Reference (CLI + Python API, auto-generated).
- Architecture overview (this document).
- Contributing guide.
- Example specs: invoice, email_category, citation, contract_obligation, incident_event.

### 6.6 Deployment Container

The production runtime image is a first-class v1 deliverable alongside the PyPI wheel.

**Image layout (multi-stage):**

1. `builder` stage: `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` (pinned by digest). Resolves `uv.lock`, exports `requirements.txt`, builds wheel into `/build/dist/`.
2. `runtime` stage: `python:3.11-slim` (pinned by digest) or `gcr.io/distroless/python3-debian12` when feasible. Copies `/build/dist/` and installs the wheel into a system venv at `/opt/prompiler/venv`.

**Runtime contract:**

- Runs as non-root UID `10001` (`prompiler:prompiler`); rootfs mounted read-only where the runtime permits (writable tmpfs for `/tmp` only).
- Default entrypoint: `prompiler serve --transport http`.
- Bind defaults to `127.0.0.1:8765`. Inside a container this is unreachable from the host, so operators must opt in to public exposure with `--host 0.0.0.0`; the change is explicit, not implicit, and is logged at startup with a WARN line.
- Health endpoint: `GET /healthz` returns `200 {"status":"ok","spec_hash":"…"}` once the spec registry has loaded. Liveness probes hit this path.
- Readiness: separate `GET /readyz` returns `503` until adapters have been initialised at least once.

**Multi-arch & distribution:**

- Built via `docker buildx` for `linux/amd64` and `linux/arm64`.
- Published to `ghcr.io/<org>/prompiler:<tag>` per release; also tagged `:latest` for the highest stable release and `:<major>.<minor>` for floating refs.
- Every image carries OCI labels: `org.opencontainers.image.source`, `…version`, `…revision`, `…licenses=Apache-2.0`, `…title=prompiler`.

**Supply-chain controls:**

- Base images pinned by digest in the `Dockerfile`; Renovate proposes upgrades.
- Trivy CVE scan runs in CI; HIGH/CRITICAL findings block the release job.
- Cosign keyless signature (GitHub OIDC identity) attached to every published image.
- SBOM (CycloneDX JSON) generated with `syft` and attached as a Cosign attestation.
- Release smoke test verifies the published image: `cosign verify`, `cosign verify-attestation`, then `docker run --rm <digest> prompiler --version`.

---

## 7. Open Items

1. Tokeniser strategy for `max_input_tokens` on backends without an official tokeniser (Gemini in particular). Interim: conservative character-count heuristic with `tokens_estimated=true` flag.
2. Pricing-table update cadence — weekly scheduled action, or only on vendor pricing change? Default to weekly for v1; revisit in v2.
3. MCP HTTP authentication strategy. v1: loopback-only + opt-in `--allow-public`. v2: bearer tokens or mTLS — out of scope here.
4. Cassette schema versioning when vendor changes wire format mid-cycle.

---

## 8. Architecture Definition of Done

- Every module in §1.2 exists with a `__init__.py` and a top-of-file docstring stating its single responsibility.
- Every public API entry in §1.3 has at least one unit test and one integration test.
- Every risk in §4 and §5 maps to at least one CI check or runtime guard.
- Every NFR from `PRD.md` §7.1 has a corresponding performance assertion in CI.
- Diagram in §1.1 stays in sync with the actual module tree (verified by a `tests/test_architecture.py` that walks `src/prompiler/` and asserts the expected top-level modules).
- This document is linked from `README.md` and from the `PLAN.md` P8 docs task list.
