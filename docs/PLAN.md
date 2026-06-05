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
- [ ] `[gap G4]` HIGH — MCP extract-over-protocol untested (only `/healthz` + 404). **Blocked on P6** — MCP tool/extract surface not implemented (P0 skeleton only). Track here, implement when P6 lands.
- [x] `[gap G1]` MEDIUM — E2E breadth: single e2e test (invoice refine-uplift) only. No e2e for tutor-decline path, other spec types, or full CLI refine flow wired to a real eval run.
- [ ] `[gap G5]` MEDIUM — Spec disk mismatch: 6 spec types tested inline but only 2 example YAMLs on disk (`invoice`, `email_category`). Loader+hash+linter path under-exercised for the inline 6.
- [ ] `[gap G2]` LOW — No `e2e` pytest marker; the lone e2e folds into the `integration` tier. Can't select/run e2e in isolation.
- [ ] `[gap G6]` LOW — Integration spec tests cover happy/coerce/reject only; thin on partial/multi-field failures and real-ish retry-then-succeed bounce.

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

- ≥ 80% coverage on `refine/` module.
- E2E test using a canned fixture proves measurable F1 uplift on at least one of the demo specs.

---

### P6 — MCP Server

**Tasks**

- Implement MCP server with `mcp` SDK.
- Expose every registered spec as a tool with proper input/output schema.
- Resources: `prompiler://specs/<name>`, `prompiler://artefacts/<name>`.
- Transports: stdio (default), HTTP (`--transport http --port N`).
- Binding policy: `127.0.0.1` by default.
- Token-usage in tool response metadata.

**Acceptance criteria**

- Claude Desktop / MCP Inspector can discover and call registered tools over stdio.
- HTTP transport works end-to-end with `curl` against the documented endpoint.
- Tool latency overhead vs direct `run()` < 20 ms p95.

**Definition of done**

- E2E MCP suite (stdio + HTTP) green in CI.
- Security review: no path traversal in resource handlers, no unbounded payload sizes, no default bind to 0.0.0.0.

---

### P7 — CLI + Observability

**Tasks**

- Full CLI surface from `PRD.md` §FR-9 (typer-based).
- `prompiler stats` reading local JSONL log.
- Cost estimate using shipped pricing table (`pricing/v1.json`).
- OpenTelemetry exporter behind `--telemetry` flag (off by default).
- Pre-commit hook config snippet documented for downstream projects.

**Acceptance criteria**

- All CLI commands have `--help` text and exit codes per Unix convention (0 ok, 1 user error, 2 internal error).
- `prompiler stats --since 7d` outputs a usage summary.
- Pricing table missing or stale produces a warning, never a hard failure.

**Definition of done**

- CLI E2E suite (subprocess-based) green in CI.
- Man-page-style docs generated from typer.

---

### P8 — Hardening, Docs, Release

**Tasks**

- Security review pass: secrets in logs, MCP bind policy, spec-file parsing (YAML unsafe loaders banned).
- Performance pass: assert all budgets from `PRD.md` §7.1 in CI.
- Evaluate codegen renderer architecture: decide whether to deepen the shared visitor (introduced P1.x) into a full FieldSpec IR module (~1.5d) or stop at FieldSpec-attached helpers (~<1d) or hold as-is. Trigger criteria: a third renderer arrives (e.g., TypeScript emitter, JSON Schema dialect variant), or a codegen-vs-runtime drift incident is recorded in `docs/LESSONS_LEARNT.md`.
- Documentation: tutorial (invoice walkthrough), reference (CLI + Python API), architecture overview, contributing guide.
- Example specs: invoice, email_category, citation, contract_obligation, incident_event.
- Versioning: `prompiler 0.1.0`, semantic-versioning policy documented.
- Release pipeline:
  - PyPI publish via OIDC trusted publisher.
  - OCI image publish to `ghcr.io/<org>/prompiler:<tag>` (multi-arch buildx), Cosign keyless signature + SBOM (CycloneDX) attestation.
  - GitHub release with wheel, sdist, SBOM, image digest manifest.

**Acceptance criteria**

- `pip install prompiler` followed by tutorial completes in < 15 min cold (PRD §9 success metric).
- All success metrics from `PRD.md` §9 measured and reported.
- No CRITICAL or HIGH issues from security-reviewer agent.

**Definition of done**

- Tagged `v0.1.0` released to PyPI.
- Docs published.
- Demo recording + tutorial walkthrough merged.

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
- `pip install prompiler==0.1.0` works on a clean Python 3.11 environment.
