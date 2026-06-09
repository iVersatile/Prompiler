# prompiler — Delivery Plan

**Status:** v1 plan locked
**Date:** 2026-05-21
**Source of truth:** `PRD.md`

---

## 1. Scope (v1)

Deliver the v1 functional surface described in `PRD.md` §6:

- EntitySpec authoring (YAML, `spec_version: 1`).
- `extract` + `classify` tasks.
- Four backend adapters: Claude, OpenAI, Gemini, Ollama.
- Pydantic v2 schema synthesis + prompt synthesis + tool-call schema synthesis.
- In-process registry with file-system discovery.
- CLI: `validate`, `compile`, `run`, `eval`, `refine`, `serve`, `registry` subcommands.
- Async-first API + sync wrappers + batch.
- MCP server (stdio + HTTP).
- Eval harness with JSON + HTML report.
- Human-in-the-loop refinement loop.
- Credential providers (env vars + Google ADC).
- Observability (structured logs + cost estimate + `prompiler stats`).
- Containerised test pipeline (Ollama sidecar + cassettes + nightly live smoke).

Anything in `PRD.md` §8 (Out of Scope) is explicitly deferred to v2+.

---

## 2. Phases

```
P0  Project foundation         (1 week)
P1  Spec & compilation core    (2 weeks)
P2  Backend adapters           (2 weeks)
P3  Runtime + registry         (1 week)
P4  Eval harness               (1.5 weeks)
P5  Refinement loop            (1 week)
P6  MCP server                 (1 week)
P7  CLI + observability        (1 week)
P8  Hardening, docs, release   (1.5 weeks)
```

Approximate calendar: 12 weeks total for a single engineer; ~6 weeks with two engineers working P1/P2 in parallel after P0.

---

## 3. Phase Detail

### P0 — Foundation

**Tasks**

- [x] Repo scaffolding: `pyproject.toml` (uv-managed), `src/prompiler/`, `tests/`, `docs/`.
- [x] Lint/format: `ruff` (lint + format), `mypy --strict`. `black` role covered by `ruff format` to avoid double-formatter drift.
- [x] CI skeleton: GitHub Actions with `unit`, `integration`, `e2e` jobs.
- [x] `docker-compose.test.yml` with Ollama sidecar + pinned model digest.
- [x] Multi-stage `Dockerfile` for production image: `uv`-based build stage + `python:3.11-slim` (or distroless) runtime, non-root UID, read-only rootfs where practical, multi-arch (`linux/amd64`, `linux/arm64`) via `docker buildx`, `/healthz` endpoint, OCI image labels (`org.opencontainers.image.{source,version,revision,licenses}`).
- [x] Apache 2.0 LICENSE, NOTICE, CONTRIBUTING, CODE_OF_CONDUCT.
- [x] Pre-commit hooks: ruff, black, mypy, `prompiler validate prompts/` (no-op until P1).
- [x] Logging skeleton (JSON Lines).
- [x] MCP server skeleton: `127.0.0.1`-bound stub exposing `/healthz` returning `200 {"status":"ok"}`. Full tool registration, resource handlers, and stdio transport deferred to P6 — this skeleton exists so every later phase can smoke-test the integration surface and CI can assert the loopback bind policy from day one.
- [x] `[plan]` Editor/git scaffolding: `.editorconfig` (charset/EOL/indent), `.gitattributes` (LF normalisation, `uv.lock`/`poetry.lock`/`package-lock.json` flagged `linguist-generated=true`, `cassettes/**` flagged `merge=union`, binary patterns), `.gitignore` (Python build artefacts, venv, lint/type caches, IDE/OS files, `docs/_archive/`, `.env*`, `out/`, `artefacts/`), `.python-version` (`3.11` pin for `uv`/`pyenv` matching `.pre-commit-config.yaml` language pin).
- [x] `[plan]` Branch model: `main` (release-only) + `dev` (daily progress). All work lands on `dev` via topic branches; release tags (`vMAJOR.MINOR.PATCH`) are cut on `main` after a `dev → main` merge. Document in `docs/RULES.md` §5.
- [x] `[plan]` GitHub branch protection on `main`: require PR, require linear history, require status checks (`unit`, `integration`, `e2e`, `pre-commit`) to pass, dismiss stale approvals on new commits, restrict force-pushes and deletions.
- [x] `[plan]` Commit `uv.lock` to the repo: this is an application, not a library; reproducible installs require the lockfile. Documented inline in `.gitignore`.
- [x] `[plan]` Hook helper scripts under `scripts/`: `scan_secrets.py` (pre-push secret scan), `check_clean_tree.py` (pre-push working-tree-clean enforcement), `check_lesson_cite.py` (commit-msg `fix:`/`perf:` LL-citation gate), `new_lesson.py` (LL-NNN register helper), `local_test.py` (local parity for CI gates).
- [x] `[plan]` Agent pointer `CLAUDE.md` at repo root referencing `docs/RULES.md` as the single source of truth for project rules; inheriting from global `~/.claude/rules/{common,python}/`.
- [x] `[plan]` Process docs in `docs/`: `RULES.md` (gates, tagging, phase boundaries), `MANUAL_TESTING.md` (local verification recipes), `LESSONS_LEARNT.md` (seeded LL register with `LL-NNN` IDs for the `fix:`/`perf:` citation gate).
- [x] `[plan]` `COMPILER_PROTOCOL_VERSION` constant in `src/prompiler/__init__.py` with bump-policy docstring. Used in `spec_hash = SHA-256(canonical_yaml(spec) || COMPILER_PROTOCOL_VERSION)` so cached artefacts survive `prompiler` patch/minor upgrades and only invalidate when the AST grammar, per-adapter projection schema, or canonical-YAML serialisation rules change.
- [x] `[plan]` `prepare-commit-msg` hook (`scripts/prepare_commit_msg.py`) auto-suggests a bare `Lesson-skip:` trailer when the staged diff is `*.md`-only; `scripts/check_lesson_cite.py` relaxes the trailer minimum-length from 10 chars to 0 for those scopes. Removes friction on docs-only `fix:`/`perf:` commits without weakening the gate for code commits.

**Acceptance criteria**

- `uv sync && uv run pytest -q` passes on empty test suite.
- CI green on first PR.
- `prompiler --help` prints CLI tree (even if subcommands stub).
- `curl http://127.0.0.1:<port>/healthz` against the skeleton server returns `200 {"status":"ok"}`; binding to any non-loopback interface requires an explicit opt-in flag and emits a WARN log line.

**Definition of done**

- All CI jobs run and pass with `--collect-only` style placeholders.
- Pre-commit installed and passing.
- README has a runnable "Hello, prompiler" stub.

---

### P1 — Spec & Compilation Core

**Tasks**

- [x] EntitySpec Pydantic model (the spec describing specs).
- [x] YAML loader + schema validation.
- [x] `spec_hash` calculator.
- [x] Pydantic model synthesiser (`pydantic.create_model`) supporting: `string`, `integer`, `decimal`, `boolean`, `date`, `datetime`, `enum`, `array`, `object` (nested), `optional`.
- [x] Cross-field constraint compiler (validators).
- [x] Prompt synthesiser: builds a prompt from spec description, field descriptions, cite flag, and few-shot block (initially empty).
- [x] JSON Schema emitter from synthesised Pydantic model.
- [x] `prompiler.compile()` entry point.
- [x] `prompiler validate` CLI subcommand. (Linter substance — `prompiler.spec.linter` — landed in P1.4; CLI wrapper added in P1.10.)
- [x] `prompiler codegen <spec>` CLI subcommand emitting `.prompiler/compiled/<name>.py` via a Jinja template. Static-codegen path complements the dynamic `pydantic.create_model` path: downstream projects vendor the generated file into their own repo for IDE autocomplete, type-checking, and offline imports without a `prompiler` runtime dependency. Generated file pins `COMPILER_PROTOCOL_VERSION` + `spec_hash` in a module-level constant so drift between vendored copy and live spec is detectable.

**Acceptance criteria**

- Invoice spec + email_category spec from PRD §5 compile to Pydantic + JSON Schema + prompt text.
- Same spec compiled twice produces byte-identical artefacts.
- `prompiler validate` catches: duplicate field names, unsupported types, missing descriptions, reserved names.
- 100% type-check clean (`mypy --strict`).

**Definition of done**

- Unit-test coverage ≥ 90% on `compiler/` module.
- Two real specs round-trip through compile + validate.
- Compile budget: < 200 ms (asserted in test).

---

### P2 — Backend Adapters

**Tasks**

- [x] `BackendAdapter` protocol.
- [x] Adapters:
  - [x] `claude`.
  - [x] `openai`.
  - [x] `gemini`.
  - [x] `ollama`.
- [x] Per-adapter `to_tool_schema(json_schema)` projection (handle backend-specific degradation: no `pattern`, no decimals, depth limits).
- [x] Credential provider abstraction + `EnvVarProvider` + `GoogleADCProvider`.
- [x] Retry policy (transient errors only) with exponential backoff.
- [ ] Per-call observability hook (latency, tokens, cost estimate via pricing table). **Deferred to P7 (CLI + Observability).** Observability — structured per-stage markers, cost estimate via `pricing/v1.json`, and `prompiler stats` — is already scoped under P7. This checkbox was misplaced P7 scope; no code landed in P2. See LL-007.
- Test infrastructure:
  - [x] Mock adapter for unit tests.
  - [x] Cassette recorder/player for paid backends (VCR-style).
  - [x] Ollama sidecar integration test running on every PR.

**Acceptance criteria**

- All four adapters pass a shared "happy-path extract" contract test.
- Adapter degradation correctly removes unsupported JSON Schema keywords.
- Missing credentials produce a clear, actionable error (no stack trace).
- Cassette tests deterministic and committed.

**Definition of done**

- ≥ 85% coverage per adapter module.
- Contract test suite green for all four backends.
- Nightly live-smoke workflow runs and passes against real APIs.

---

### P3 — Runtime + Registry

**Tasks**

- [x] In-process registry (`prompiler.registry`).
- [x] File-system discovery (scan `prompts/`).
- [x] Programmatic registration.
- [x] `run()` / `run_sync()` orchestration: select adapter → call → validate → retry-once-on-validation-error → return typed instance.
- [x] `run_batch()` with `asyncio.Semaphore` and per-item isolation.
- [x] Doc-size guardrail (`max_input_tokens` enforcement).
- [x] `ExtractionFailed` exception hierarchy.

**Acceptance criteria**

- `prompiler.run("invoice", text, backend="ollama")` returns a validated `Invoice` instance against a real Ollama backend.
- `run_batch` of 100 items returns 100 results with partial failures isolated.
- Validation-retry produces a corrective message and the second attempt succeeds on a forced-failure fixture.

**Definition of done**

- [x] ≥ 85% coverage on `runtime/` module.
- [x] Stress test: 100 concurrent batch calls do not exceed memory budget (asserted with `tracemalloc`).
- [x] Failure-mode tests cover: missing field, type mismatch, refusal, timeout, rate limit.

---

### P4 — Eval Harness

**Tasks**

- [x] YAML fixture loader.
- [x] Eval runner: iterate fixtures → run extraction → diff against expected → compute per-field precision / recall / F1 + overall metrics.
- [x] Cost + token accounting per run.
- [x] `eval-report.json` emitter (with `spec_hash`, backend, model, timestamp).
- [x] `eval-report.html` static dashboard (zero JS framework; one vanilla JS file for table sort + filter).
- [x] `prompiler eval` CLI subcommand.
- [x] `[plan]` Zero-dep fuzzy fallback for nested-array eval matching: token-set Jaccard similarity at threshold ≥ 0.85, activated only on records that score F1 = 0 under exact match. Catches near-miss extractions (trailing whitespace, punctuation drift, minor reordering) without pulling an embedding model or GPU dependency. Reported as a separate `fuzzy_f1` column alongside `exact_f1` so the signal stays auditable.

**Acceptance criteria**

- Eval against a 10-case fixture completes in < 90 s on Ollama.
- Report includes per-field metrics, per-case diffs, aggregate, and run metadata.
- HTML report opens in a browser with no console errors and Lighthouse a11y ≥ 95.
- `spec_hash` mismatch between fixture and current spec emits a warning.

**Definition of done**

- [x] ≥ 85% coverage on `eval/` module.
- [x] Snapshot test for HTML report (golden-file diff).
- [x] HTML report verified at viewport 320, 768, 1440.

**Test-coverage hardening (gap backlog)**

Gap evaluation of the e2e + integration suites (2026-06-05) surfaced the
following. Worked in severity order (HIGH → LOW). Items G4/G6 may be blocked on
later phases; noted inline.

- [x] `[gap G3]` HIGH — Real backend not exercised in integration tier. `test_integration_backend_swap.py` uses scripted doubles only; claude/openai/gemini have unit + cassette coverage but no integration test driving a real adapter through the orchestrator against recorded wire bytes. Add a cassette-backed integration test.
- [x] `[gap G4]` HIGH — MCP extract-over-protocol untested (only `/healthz` + 404). **Closed in P6** — `tests/test_e2e_mcp.py` drives discover/extract/resource-read over an in-memory `ClientSession` (full handshake + serialization).
- [x] `[gap G1]` MEDIUM — E2E breadth: single e2e test (invoice refine-uplift) only. No e2e for tutor-decline path, other spec types, or full CLI refine flow wired to a real eval run.
- [x] `[gap G5]` MEDIUM — Spec disk mismatch: 6 spec types tested inline but only 2 example YAMLs on disk (`invoice`, `email_category`). Loader+hash+linter path under-exercised for the inline 6.
- [x] `[gap G2]` LOW — No `e2e` pytest marker; the lone e2e folds into the `integration` tier. Can't select/run e2e in isolation.
- [x] `[gap G6]` LOW — Integration spec tests cover happy/coerce/reject only; thin on partial/multi-field failures and real-ish retry-then-succeed bounce.
- [x] `[gap G7]` MEDIUM — `chunk_for_extract()` (PRD FR-13) unimplemented, not merely untested. PRD/architecture name it a v1-shipped utility and `runtime/__init__.py:4` docstring promises "the chunking helper", but no `def chunk*` exists anywhere in `src/` and no test references it. **Feature gap** — needs implementation + tests, not just a test. Severity MEDIUM: it is a standalone helper (no auto-chunk wiring required by FR-13), so nothing downstream is currently broken by its absence. **Closed** — `src/prompiler/runtime/chunk.py` ships the char-window splitter (reuses `_CHARS_PER_TOKEN` to stay in lockstep with `_check_doc_size`); exported from `runtime/__init__.py`; 11 unit tests in `tests/test_runtime_chunk.py`.

---

### P5 — Refinement Loop

**Tasks**

- [x] Patch generator: feeds eval report + current prompt to a "tutor" LLM call; emits unified diff of prompt text.
- [x] Diff applier (human-confirm flow) with unified-diff preview.
- [x] Re-run eval and surface metric delta.
- [x] Refusal-mode handling: if tutor declines, surface error and exit non-zero.

**Acceptance criteria**

- Forced-regression fixture: degrade prompt by hand → `refine` proposes a patch that restores ≥ original F1.
- Diff preview shown before any file write.
- v1 never modifies prompt without explicit user confirmation.

**Definition of done**

- [x] ≥ 80% coverage on `refine/` module. — **met**: full-suite coverage 98% (`differ` 96%, `reeval` 100%, `tutor` 100%, `__init__` 100%); 701 passed.
- [x] E2E test using a canned fixture proves measurable F1 uplift on at least one of the demo specs. — **met**: `test_e2e_refine_uplift.py::test_refine_restores_f1_on_invoice_spec` drives invoice F1 0.0 → 1.0 with `delta.improved is True` and `after.metrics.f1 >= before.metrics.f1`; `..._contact_spec` + decline-path floor test also green.

Phase-done (RULES §7): user approved 2026-06-06. CI green on `feat/p5-refinement-loop` (run 27060873115); §9 local gate 3 pass / 1 skip (structured-logging P2-deferred).

---

### P6 — MCP Server

**Tasks**

- [x] Implement MCP server with `mcp` SDK.
- [x] Expose every registered spec as a tool with proper input/output schema.
- [x] Resources: `prompiler://specs/<name>`, `prompiler://artefacts/<name>`.
- [x] Transports: stdio (default), HTTP (`--transport http --port N`).
- [x] Binding policy: `127.0.0.1` by default.
- [x] Token-usage in tool response metadata.
- [x] Close `[gap G4]` (P4 backlog): add integration test driving MCP extract-over-protocol end-to-end (not just `/healthz` + 404). Flip the G4 checkbox in the P4 gap list when done.

**Acceptance criteria**

- [x] Claude Desktop / MCP Inspector can discover and call registered tools over stdio. — in-memory `ClientSession` exercises the shared discover/call protocol path (`test_e2e_mcp.py`).
- [x] HTTP transport works end-to-end with `curl` against the documented endpoint. — `streamable-http` wiring + `/healthz` mount + loopback guard covered (`test_mcp_main.py`).
- [x] Tool latency overhead vs direct `run()` < 20 ms p95. — measured p95 overhead 0.855 ms.

**Definition of done**

- [x] E2E MCP suite (stdio + HTTP) green in CI.
- [x] `[gap G4]` closed: MCP extract-over-protocol covered by an integration test, G4 checkbox flipped in the P4 gap list.
- [x] Security review: no path traversal in resource handlers, no unbounded payload sizes, no default bind to 0.0.0.0. — resource handlers do no FS I/O (registry `^[a-z0-9_-]+$` key + KeyError); tool `text` bounded by `MAX_TEXT_BYTES` (1 MiB); default bind `127.0.0.1`, non-loopback env-gated.

Phase-done (RULES §7): user approved 2026-06-07. §9 local gate green — unit 611 passed, mypy clean (48 files), ruff clean; latency acceptance PASS (0.855 ms p95 overhead).

---

### P7 — CLI + Observability

**Tasks**

- [x] Full CLI surface from `PRD.md` §FR-9 (typer-based).
- [x] `prompiler stats` reading local JSONL log.
- [x] Cost estimate using shipped pricing table (`pricing/v1.json`).
- [x] OpenTelemetry exporter behind `--telemetry` flag (off by default).
- [x] Pre-commit hook config snippet documented for downstream projects.

**Acceptance criteria**

- [x] All CLI commands have `--help` text and exit codes per Unix convention (0 ok, 1 user error, 2 internal error). — subprocess E2E asserts per-command `--help` exit 0; validate 0/1/2, stats 0/1, codegen 2 (`test_e2e_cli.py`).
- [x] `prompiler stats --since 7d` outputs a usage summary. — `test_cli_stats.py` + E2E 7d-window summary assertion.
- [x] Pricing table missing or stale produces a warning, never a hard failure. — degrade-never loader warns on missing/schema-mismatch (`test_pricing_loader.py`).

**Definition of done**

- [x] CLI E2E suite (subprocess-based) green in CI. — `test_e2e_cli.py`, e2e job green on PR #25 + main.
- [x] Man-page-style docs generated from typer. — `docs/CLI.md` regenerated via `typer ... utils docs`; regen note in README.

Phase-done (RULES §7): user approved 2026-06-08. PR #25 merged to main (`4a2ff3d`); remote CI green on main (pre-commit, unit, integration, e2e). All tasks + acceptance + DoD met.

---

### P8 — Hardening, Docs, Release

**Tasks**

- Security review pass: secrets in logs, MCP bind policy, spec-file parsing (YAML unsafe loaders banned).
- Performance pass: assert all budgets from `PRD.md` §7.1 in CI.
- Evaluate codegen renderer architecture: decide whether to deepen the shared visitor (introduced P1.x) into a full FieldSpec IR module (~1.5d) or stop at FieldSpec-attached helpers (~<1d) or hold as-is. Trigger criteria: a third renderer arrives (e.g., TypeScript emitter, JSON Schema dialect variant), or a codegen-vs-runtime drift incident is recorded in `docs/LESSONS_LEARNT.md`.
- Documentation: tutorial (invoice walkthrough), reference (CLI + Python API), architecture overview, contributing guide.
- Example specs: invoice, email_category, citation, contract_obligation, incident_event.
- Versioning: `prompiler 0.1.1`, semantic-versioning policy documented.
- Release pipeline:
  - PyPI publish via OIDC trusted publisher.
  - OCI image publish to `ghcr.io/<org>/prompiler:<tag>` (multi-arch buildx), Cosign keyless signature + SBOM (CycloneDX) attestation.
  - GitHub release with wheel, sdist, SBOM, image digest manifest.

**Acceptance criteria**

- `pip install prompiler` followed by tutorial completes in < 15 min cold (PRD §9 success metric).
- All success metrics from `PRD.md` §9 measured and reported.
- No CRITICAL or HIGH issues from security-reviewer agent.

**Definition of done**

- Tagged `v0.1.1` released to PyPI.
- Docs published.
- Demo recording + tutorial walkthrough merged.

Phase-done (RULES §7): user approved 2026-06-08. `v0.1.1` released to PyPI (sole release) and GitHub release `v0.1.1` published; demo recording + tutorial walkthrough merged via PR #31 (`80b6c4e`). All P8 tasks + acceptance + DoD met.

---

### P9 — Determinism gap closure & anti-drift hardening (post-v1, pre-v2)

Closes a documented-but-unimplemented gap found in the v1-vs-PRD adversarial review: run-time determinism (PRD §3/§5, FR-2, FR-14; architecture.md L166-179; MANUAL_TESTING.md L187-197) is specified but absent from `src/`. The `BackendAdapter` protocol never carried `temperature`/`seed`/`supports()` — P2 design-lock B narrowed `call(...)` down to `extract(*, prompt, json_schema, timeout)` and silently dropped them. P9.1 implements the missing surface; P9.2 installs the process gates that would have caught the drift. P9.1 runs **before** P9.2.

#### P9.1 — Determinism fix (NEXT TASK)

**Tasks**

- Extend `BackendAdapter` protocol (`backends/base.py`): add `temperature: float = 0.0` and `seed: int | None = 42` to `extract`; add `supports(feature: str) -> bool`.
- Change `extract` return type to `ExtractResult(data: dict, system_fingerprint: str | None, deterministic: bool)`; preserve the latency/fingerprint that `_pipeline.post_with_retry` already surfaces but `extract` currently discards.
- Per-adapter seed matrix:
  - **ollama:** thread `options.seed` + `options.temperature`; `supports("seed") -> True`.
  - **openai:** thread `seed` + `temperature`; capture `system_fingerprint` from response; `supports("seed") -> True`.
  - **claude:** thread `temperature` only; `supports("seed") -> False`.
  - **gemini:** thread `temperature` only; `supports("seed") -> False`.
- Orchestrator (`runtime/orchestrator.py`): unwrap `ExtractResult.data` at validation, trace-tag `deterministic`, emit one-shot WARN per non-seed backend per process.
- Config wiring: plumb `[tool.prompiler]` temperature/seed defaults through `run` / `run_sync` / `run_batch`.

**Acceptance criteria**

- Shared adapter contract test asserts `temperature`/`seed` params present and `supports()` returns the matrix above.
- Cassette proves `seed` lands in the wire payload for seed-capable adapters (ollama, openai).
- Trace-tagging test confirms `deterministic` tag set per call.
- `system_fingerprint` captured and surfaced for openai.
- FR-2 and FR-14 each map to ≥1 passing test (entry added to the P9.2 traceability matrix).

**Definition of done**

- Coverage ≥ 85% on changed modules; `mypy --strict` clean; ruff clean.
- MANUAL_TESTING Ollama recipe reproduces byte-identical output at `temperature=0`, `seed=42`.
- `docs/LESSONS_LEARNT.md` entry recording the determinism-drift incident.

#### P9.2 — Anti-drift hardening (AFTER P9.1)

**Tasks**

- Build an FR↔test traceability matrix; CI fails when any functional FR maps to zero tests.
- Adapter contract test asserts required params by signature introspection (not just attribute presence).
- `docs/RULES.md`: require any design-lock or contract-narrowing change to cite affected FRs in the PR description.
- `docs/RULES.md`: forbid "defer to manual testing" for **functional** FRs (perf-timing deferral, per `test_perf_budgets.py` L10-13, stays allowed).
- Extend the per-phase acceptance gate (`docs/RULES.md` §7) to require traceability-matrix green.

**Acceptance criteria**

- Matrix exists and is wired into CI; a deliberately untested FR fails the build in a dry run.
- Contract test fails if an adapter omits a required `extract` parameter.
- `docs/RULES.md` updated with both the FR-citation rule and the functional-FR no-defer rule.

**Definition of done**

- CI traceability gate green on a clean tree; documented in `docs/RULES.md` §7.
- All v1 functional FRs present in the matrix with at least one passing test each.

---

## 4. Cross-Cutting Tasks

These run in every phase, not only in P8.

- Every PR: unit + integration + e2e jobs green.
- Every PR: `mypy --strict` clean, ruff clean, coverage ≥ phase target.
- Every PR touching adapters: cassette regenerated or unchanged (diff inspected).
- Nightly: live-smoke against real APIs; failure pages on-call (or files an issue at minimum in v1).
- Weekly: dependency audit (`uv pip audit` or equivalent).

---

## 5. Test Strategy Summary

| Tier | Scope | When | Backends |
|------|-------|------|----------|
| unit | pure functions, compiler, schema synth | every push | mocks |
| integration | adapter contracts, runtime | every push | Ollama sidecar + cassettes |
| e2e | CLI, MCP, refinement loop | every PR | Ollama + cassettes |
| live-smoke | real-API drift detection | nightly + manual | real Claude / OpenAI / Gemini |

Speed budget per tier: unit < 30 s, integration < 2 min, e2e < 5 min, live-smoke < 10 min.

---

## 6. Risks & Mitigations

| Risk | Phase | Mitigation |
|------|-------|------------|
| Vendor SDK breaking changes | P2 onward | Pin majors; cassettes catch unintentional changes; live-smoke catches vendor changes. |
| Pydantic JSON Schema → Claude/OpenAI tool-schema rejection | P2 | Adapter degradation layer + explicit unit tests per backend. |
| Ollama model availability | P0/P2 | Pin digest in docker-compose; vendor into test image cache. |
| Eval HTML report bloat | P4 | Static-only output, hard size budget (< 200 kB gzipped). |
| MCP spec evolves | P6 | Pin MCP SDK version; track changelog. |
| Cassette rot | P2 onward | Cassette refresh checklist in PR template when adapter code changes. |
| Container supply-chain compromise (base image CVEs, registry takeover, image tampering) | P0/P8 | Pin base image by digest; Trivy scan in CI; Cosign keyless signing + SBOM attestation on every published image; verify signature in release smoke test. |

---

## 7. Definition of Done — v1 (whole product)

- All eight phase DoDs met.
- All `PRD.md` §6 functional requirements pass an acceptance test.
- All `PRD.md` §7 non-functional requirements asserted in CI.
- All `PRD.md` §9 success metrics measured and recorded.
- Apache 2.0 LICENSE, NOTICE, and patent grant present and correct.
- `pip install prompiler==0.1.1` works on a clean Python 3.11 environment.
