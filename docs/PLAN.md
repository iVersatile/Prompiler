# prompiler — Delivery Plan (v2)

**Status:** v2 plan locked
**Date:** 2026-06-09
**Source of truth:** `PRD.md` §8.2 (v2 accepted set)
**Predecessor:** v1 delivered (`v0.1.1`, all phases P0–P8 closed). Full v1 plan archived in `PLAN.BK.MD`.

> Plan locked as of 2026-06-09. §1 scope and §2 phase order are committed.
> Open questions in §3 are all RESOLVED. Per-phase task checkboxes are added at
> each phase boundary (§6 Phase Start Gate); Q1 is expanded below.

---

## 0. Prerequisites (carry-over from v1)

These must be resolved or explicitly re-deferred **before** v2 phase work begins.

| ID | Item | State entering v2 | Proposed v2 action |
|----|------|-------------------|--------------------|
| **#8** | **RECOVERED:** "LL-NNN entry for CI exemption fix" — write a Lessons Learnt entry capturing *why* the branch-guard needed a CI no-op | Open. The fix shipped (`check_branch_guard.py` no-ops when `CI=true`/`GITHUB_ACTIONS=true`, PR #17 `a85df0a`→`5aa0f55`; spec in RULES.md §1.0 via PR #18). LL-005 covers the *separate* truthy-escape-hatch bug, **not** the CI exemption. | **DONE: LL-009 written** (CI-exemption lesson; LL-008 was already taken by the determinism entry). Captures the rationale; fix already shipped + spec'd. |
| **codegen IR** | Deepen shared `walk.py` visitor → FieldSpec IR module | Deferred; no trigger fired | Becomes **live** if a v2 phase adds a 3rd renderer (e.g. TypeScript / JSON-Schema-dialect emitter). Decide at that phase boundary. |

Closed in v1, no carry-over: #3 (MCP extract test — landed via P6/G4), #4, #5, #6, #7.

---

## 1. Scope (v2) — accepted set

Theme = **Mixed**: breadth headline (multi-modal + auto-refinement) carried on
depth fillers (cache, keychain, composition, streaming). All rows trace to
`PRD.md` §8 / §3 / FR-7 / FR-10 / FR-14 — see §5 cross-check.

**Breadth headline** (the two features that define v2):

| Feature | Source | Blast radius |
|---------|--------|-------------|
| Multi-modal input (images, audio) | PRD §8, §3 | **LARGE** — spec schema + all 4 adapter payloads |
| Auto-apply refinement (policy-gated) | PRD §8, FR-7 | **MEDIUM** — builds on P5 refine loop; adds `--auto-apply` + threshold/iteration guard |

**Depth fillers**, listed in the maintainer's chosen **risk order** (smallest
blast radius first):

| # | Feature | Source | Blast radius |
|---|---------|--------|-------------|
| 1 | Compile-result cache | PRD §8, FR-14 | **SMALL** — memoize `compile()` keyed on `(spec_hash, backend, model, input_hash)`; no schema/adapter change |
| 2 | Keychain / OAuth credential flows | PRD §8, FR-10 | **MEDIUM** — new credential resolver alongside env-var + ADC; no spec/codegen change |
| 3 | Spec composition / inheritance | PRD §8, §3 | **LARGE** — loader + validator + `spec_hash` + codegen walk; forces the `spec_version 1→2` question (§3 Q3) |
| 4 | Streaming responses | PRD §8, §3 | **LARGE** — all 4 adapters + runtime contract + orchestrator retry; `extract` becomes async-iterator |

**Out of v2 (OSS core):**

| Candidate | Source | Disposition |
|-----------|--------|-------------|
| Hosted SaaS UI | PRD §8 | **Drop from OSS core** — product/hosting concern, not the compiler. Revisit as a separate non-OSS track. |
| Code of Conduct (custom vs verbatim CC 2.1) | PRD §12 | **Not product scope** — governance only; trigger = first external contributor PR OR maintainer team >3. |

---

## 2. Phases (v2) — Mixed sequence

**Target version: Single `v0.2.0`.** All v2 scope batches into one tagged
release — no incremental `v0.1.x` feature-flag trickle. (Resolves §3 Q4.)

Phase order pairs one breadth feature with one depth filler per phase, so each
phase ships a headline capability plus a low-risk win. Risk rises across phases.

```
Q0  Prerequisites clearance
      - #8 decision: DONE — LL-009 written (CI-exemption lesson; LL-008 was the determinism entry)
      - PRD §8 candidates promoted into §6 (in-scope) — PRD update
      - codegen-IR trigger watch armed (fires if any phase adds a 3rd renderer)

Q1  Multi-modal input  +  Compile-result cache
      - breadth: image/audio fields in spec schema + 4 adapter payloads
      - depth (SMALL): memoized compile keyed on spec_hash tuple

Q2  Auto-apply refinement  +  Keychain / OAuth
      - breadth: --auto-apply re-runs eval to threshold / N iterations (FR-7)
      - depth (MEDIUM): KeychainProvider + OAuthProvider (FR-10)

Q3  Spec composition / inheritance
      - depth (LARGE): loader + validator + spec_hash; bumps spec_version 1→2
      - flatten-before-walk: loader resolves inheritance into one flat spec
        BEFORE walk.py; walk contract unchanged → codegen-IR stays un-fired
      - clean break: version:1 errors → one-shot `prompiler migrate-spec`

Q4  Streaming responses
      - depth (LARGE): all 4 adapters + runtime contract + orchestrator retry
      - extract becomes async-iterator; highest blast radius → last

Q5  Hardening, docs, release → single v0.2.0 tag
      - hardening: secret/bandit audit + perf-budget verify + coverage/mypy sweep
      - docs: refresh README/TUTORIAL/CLI/API/architecture to shipped v2 surface
      - release: bump 0.1.3→0.2.0, changelog, dry-run; tag via §5 (user-approved)
```

**Staffing:** solo today, but phase boundaries and blast-radius isolation are
designed so work can fan out to >1 engineer. Independent-blast-radius pairs
(e.g. Q1 cache vs Q2 keychain) parallelize cleanly; LARGE items (multi-modal,
composition, streaming) stay owned by one engineer each to avoid merge churn.
**No target dates** — phases are a logical/dependency order, not a calendar.

---

## 2.1 Q1 — Multi-modal input + Compile-result cache (expanded)

**Phase tag base for §6 gate:** `v0.1.1`. **Blast radius:** LARGE (multi-modal)
+ SMALL (cache). Multi-modal is single-owner; cache is independent and can fan
out. PRD anchors: §8.2 (multi-modal, cache), FR-14 (cache key).

**Scope correction (from codebase grounding):** `compile_spec(spec)` takes the
spec *only* — its artefacts are a pure function of the spec, so the compile-side
cache keys on **`spec_hash` alone**. The full FR-14 tuple
`(spec_hash, backend, model, input_hash)` keys the **runtime `extract` result**
(the model call), not `compile`. The two caches are separate layers; Q1 ships
both but does not conflate their keys.

### Track A — Multi-modal input (LARGE, single-owner)

- [x] **A1. Spec schema: add modal field types.** Extend `FieldType` /
  `EntitySpec` in `src/prompiler/spec/model.py` to accept image/audio input
  declarations, with `extra="forbid"` invariants and a `model_validator` clause.
  *Exit:* new validator unit tests pass (valid modal spec loads; malformed modal
  field raises `ValueError`); existing spec tests stay green.
- [x] **A2. spec_hash covers modal fields.** Confirm `spec_hash` digest changes
  when a modal field is added/removed (canonical-YAML folds the new keys).
  *Exit:* round-trip test asserts hash inequality across modal-vs-text spec.
- [x] **A3. COMPILER_PROTOCOL_VERSION decision.** Modal fields change per-adapter
  projection schema → bump `COMPILER_PROTOCOL_VERSION` in
  `src/prompiler/__init__.py` (RULES.md §10). *Exit:* version-bump rationale
  recorded in commit body; protocol-version test updated.
- [x] **A4. Adapter payloads — 4 backends.** Thread modal content through
  `BackendAdapter.call` payload construction for claude/openai/gemini/ollama
  (`src/prompiler/backends/*.py`); gate via `supports("multimodal")`. *Exit:*
  per-adapter payload tests assert correct modal block shape; unsupported
  backend raises a clear capability error, not a silent drop.
- [x] **A5. Orchestrator + MCP surface.** Plumb modal input through
  `runtime/orchestrator.py` and the MCP `extract` tool. *Exit:* integration test
  runs a modal extract end-to-end against the mock adapter.

### Track B — Compile-result cache (SMALL, independent)

- [x] **B1. Compile-side memoization.** Memoize `compile_spec` keyed on
  `spec_hash`. *Exit:* second `compile_spec` on a field-equal spec returns a
  cache hit (observable via hook/metric); artefacts stay field-equal
  (determinism contract preserved).
- [x] **B2. Runtime result cache (FR-14).** Cache `extract` results keyed on
  `(spec_hash, backend, model, input_hash)`. *Exit:* repeated identical extract
  is served from cache (no adapter `call`); any tuple-element change misses.
- [x] **B3. Cache invalidation + opt-out.** Cache respects `spec_hash` /
  protocol-version changes automatically; expose a disable switch. *Exit:* test
  shows a protocol-version bump invalidates stale entries; disable flag forces
  recompute.

### Q1 exit criteria (phase-done, feeds §7 gate)

- [x] All A* and B* boxes checked; full suite green; coverage ≥ 80%.
- [x] mypy strict clean across touched modules.
- [x] `COMPILER_PROTOCOL_VERSION` bump (A3) reflected in any golden fixtures.
- [x] No prompt/response payloads logged below `trace` (RULES.md §8) — modal
  bytes are payloads, audit the new adapter code for leakage.

---

## 2.2 Q2 — Auto-apply refinement + Keychain/OAuth (expanded)

**Phase tag base for §6 gate:** `main` @ `23a8b0b` (Q1 close — single-`v0.2.0`
policy means Q1 ships no tag, so the gate base is the merge commit, not a tag).
**Blast radius:** MEDIUM (auto-apply: new disk write + loop driver) + MEDIUM
(credentials: keychain read + OAuth token store). Both single-owner; the two
tracks share no module, so they parallelize cleanly. PRD anchors: FR-7
(`--auto-apply`), FR-10 (`KeychainProvider`, `OAuthProvider`).

**Scope grounding (from codebase + 3 resolved spec gaps):**

- **GAP1 — stop metric.** `run_eval` (`src/prompiler/eval/runner.py:177`) returns
  an `EvalResult` carrying aggregate `Metrics` (precision/recall/F1). The
  auto-apply loop reads **aggregate F1** as its stop signal. Defaults:
  `--max-iterations 3`, no-improve ε `0.01`, `--threshold` **explicit/required**
  (no implicit default — caller must state the target).
- **GAP2 — apply is a new write.** `refine` today only *prints* a diff:
  `_cmd_refine` (`cli.py:443`) calls `propose_patch_sync` (`cli.py:468`) then
  `sys.stdout.write(diff)` (`cli.py:480`). It never mutates a file. `--auto-apply`
  adds the **first in-place spec write**, so it is guarded by a dirty-tree refusal
  (git is the undo layer).
- **GAP3 — OAuth grant is async-by-nature, `resolve()` is sync.**
  `CredentialProvider.resolve(self, backend) -> Credential`
  (`src/prompiler/backends/credentials.py:56`) is **synchronous**. Interactive
  OAuth grant (browser round-trip) cannot live inside it, so the grant moves to a
  separate `prompiler login` command; `resolve()` only reads/refreshes a
  pre-primed token store.

### Track C — Auto-apply refinement (MEDIUM, FR-7)

- [x] **C1. Loop driver.** Add `--auto-apply` to the `refine` command
  (`cli.py:215` / `_cmd_refine` `cli.py:443`). Loop: apply patch → `run_eval`
  (`eval/runner.py:177`) → read aggregate F1 → repeat until a stop condition.
  *Exit:* a ≥2-iteration scripted loop runs end-to-end; plain `refine` (no flag)
  prints-diff-only, behaviour unchanged.
- [x] **C2. Stop conditions.** Metric = aggregate F1; `--threshold` explicit and
  required; `--max-iterations` default `3`; ε `0.01` no-improvement guard halts a
  stalled loop. *Exit:* one test each for threshold-hit, max-iterations-hit, and
  ε-stall exit.
- [x] **C3. Apply-to-file.** In-place spec write between rounds. **Refuse on a
  dirty git tree** unless `--force` (git-as-undo; no silent overwrite). *Exit:*
  file mutates across a clean-tree run; a dirty tree aborts before any write;
  `--force` overrides the refusal.

### Track D — Keychain / OAuth (MEDIUM, FR-10)

- [x] **D1. KeychainProvider.** Sync `resolve` reads credentials from the OS
  keychain, conforming to the existing `CredentialProvider` Protocol
  (`credentials.py:56`). *Exit:* a faked keychain resolves a `Credential`; a
  missing entry raises `CredentialError` carrying `DOCS_REF`.
- [x] **D2. OAuthProvider + `prompiler login`.** Sync `resolve` returns a
  cached/refreshed token from the token store; interactive grant lives in a
  separate `prompiler login` command that primes that store. *Exit:* headless
  `resolve` returns a primed token; an expired token triggers refresh; an
  un-primed store raises a "run `prompiler login`" error.
- [x] **D3. Provider selection.** Resolve which provider is active via the
  existing precedence chain (kwarg → env → `[tool.prompiler]` pyproject). *Exit:*
  a precedence test asserts kwarg beats env beats pyproject default.

### Q2 exit criteria (phase-done, feeds §7 gate)

- [x] All C* and D* boxes checked; full suite green; coverage ≥ 80%.
- [x] mypy strict clean across touched modules.
- [x] No credentials/tokens in stdout/stderr or logged below `trace` (RULES.md §8)
  — audit `login`, keychain read, and OAuth refresh paths.
- [x] `--auto-apply` writes are git-tracked and reversible; no silent overwrite on
  a dirty tree.

---

## 2.3 Q3 — Spec composition / inheritance (expanded)

**Phase tag base for §6 gate:** `main` @ `cfdbea0` (Q2 close — single-`v0.2.0`
policy means Q2 ships no tag, so the gate base is the Q2-close commit, not a tag).
**Blast radius:** LARGE — single-owner. One feature touches the spec model,
loader, hash, and CLI plus a data migration of every repo example; no independent
sub-track to fan out, so it stays owned by one engineer to avoid merge churn.
PRD anchors: §8 Out of Scope; §3 (removes the v1 "single flat spec" limit).

**Scope grounding (from codebase):**

- **Model.** `EntitySpec` (`src/prompiler/spec/model.py:97`) pins
  `spec_version: Literal[1]` (L102) and sets `extra="forbid"` on all four models;
  there is **no inheritance field** today. Q3 bumps the literal to `2` and adds an
  optional `extends`.
- **Loader.** `load_spec(path) -> EntitySpec` (`src/prompiler/spec/loader.py:91`)
  has **no inheritance logic** — it parses one file straight into one
  `EntitySpec`. Q3 adds a **flatten pass**: resolve `extends` into a single flat
  `EntitySpec` *before* returning, so downstream contracts are untouched.
- **Walk contract stays flat.** `walk_field` / `walk_fields`
  (`src/prompiler/compiler/walk.py:34`) and `FieldVisitor[T]` (L13) expect a flat
  spec. Flatten-before-walk means the walk sees the same shape it always has →
  **no new emitter, codegen-IR stays un-fired** (§3 q5).
- **Hash folds for free.** `spec_hash` (`src/prompiler/spec/hash.py:41`) digests
  `canonical_yaml(spec)` + `COMPILER_PROTOCOL_VERSION`. Because it runs on the
  loader's *output*, flatten-before-walk makes that output the flattened spec, so
  the hash already folds over the resolved form with no call relocation (§3 q3).
- **Migration surface.** Typer CLI (`src/prompiler/cli.py`) uses
  `@app.command()` + private `_cmd_*` handlers; the nine `examples/*.yaml` are all
  `spec_version: 1` and must migrate in this PR.

### Track E — Composition core (LARGE, single-owner)

- [x] **E1. spec_version 1→2 clean break.** Bump
  `spec_version: Literal[1]` → `Literal[2]` (`spec/model.py:102`); a `version: 1`
  spec must error with a pointer to `prompiler migrate-spec` (no dual-path
  loader). *Exit:* a `version: 1` spec raises `SpecLoadError` naming
  `migrate-spec`; a `version: 2` spec loads; `extra="forbid"` invariants intact.
- [x] **E2. `extends` inheritance field.** Add an optional `extends` (parent spec
  reference) to `EntitySpec`, preserving `extra="forbid"`. *Exit:* a spec with
  `extends` validates; a malformed `extends` raises `ValueError`; a spec without
  `extends` still loads (field is optional).
- [x] **E3. Flatten-before-walk loader pass.** `load_spec` resolves `extends` into
  **one flat `EntitySpec`** before returning — child fields override parent,
  merge order well-defined, inheritance cycles detected. *Exit:* a parent+child
  pair flattens to the expected merged field set; a cycle raises `SpecLoadError`;
  `walk.py` runs unchanged on the flattened spec (no walk edits).
- [x] **E4. spec_hash over the flattened form.** Confirm `spec_hash` digests the
  loader's flattened output so cache keys track parent changes (resolves §3 q3 =
  YES). *Exit:* editing a parent changes the child's `spec_hash`; two specs that
  flatten field-equal share a hash. **No field provenance metadata** (§3 q —
  DEFER): the flattener carries no origin record.

### Track F — Migration + examples (MEDIUM)

- [x] **F1. `prompiler migrate-spec` command.** One-shot Typer command that
  rewrites a `version: 1` spec to `version: 2` in place. *Exit:* `migrate-spec`
  on a v1 file yields a loadable v2 file; running it on an already-v2 file is a
  safe no-op with a clear message (idempotent).
- [x] **F2. Migrate repo examples + fixtures.** Convert all nine
  `examples/*.yaml` to `version: 2` via `migrate-spec`; update any golden
  fixtures the bump touches. *Exit:* every example loads under the v2 loader; the
  example/e2e suites stay green.

### Q3 exit criteria (phase-done, feeds §7 gate)

- [x] All E* and F* boxes checked; full suite green; coverage ≥ 80%.
- [x] mypy strict clean across touched modules.
- [x] `walk.py` contract unchanged — no new emitter/renderer fired; codegen-IR
  stays un-fired (§3 q5).
- [x] `spec_hash` folds over the flattened form (E4); a parent edit invalidates
  the child's cache key.
- [x] No `version: 1` spec silently accepted; every repo example migrated to
  `version: 2`.
- [x] Field provenance NOT populated (§3 DEFER) — flattener attaches no origin
  metadata; revisit only when a consumer needs it.

---

## 2.4 Q4 — Streaming responses (expanded)

**Phase tag base for §6 gate:** `main` @ `5181e4f` (Q3 close — single-`v0.2.0`
policy means Q3 ships no tag, so the gate base is the Q3-close commit, not a tag).
The pre-Q4 residual-finding fixes this gate surfaced (PRs #49/#50,
`149c968`…`7051067`) are already merged; Q4 branches from `7051067`.
**Blast radius:** LARGE — single-owner, highest of the v2 phases. One change
narrows the `extract` contract across the Protocol, all four adapters, the
orchestrator retry path, the result cache, and the MCP surface; no independent
sub-track to fan out, so it stays owned by one engineer to avoid merge churn.
PRD anchors: §8 Out of Scope; §3 v1 non-goals (streaming).

**Scope grounding (from codebase):**

- **Protocol.** `BackendAdapter` (`src/prompiler/backends/base.py:84`) exposes one
  terminal method `async def extract(...) -> ExtractResult` (L93-102) returning a
  single `ExtractResult` dataclass (L61-82); capability gating is `supports(feature)`
  (L104-118, e.g. `"seed"`, `"multimodal"`). Q4 adds a streaming surface plus a
  `"streaming"` capability flag — a **contract narrowing → §7.1 FR-citation**.
- **No async iterators today.** All four adapters and the orchestrator are
  `async def` but value-returning; a codebase grep finds **no** `AsyncIterator` /
  `async for` / `yield` (only comments). Q4 introduces the first one.
- **SSE adapters.** Claude (`backends/claude.py:115`), OpenAI (`openai.py:109`),
  Gemini (`gemini.py:125`) each issue a single `post_with_retry()` POST and parse
  one response body for the forced tool-use / function-call block. Streaming means
  consuming Server-Sent-Event deltas and assembling the same final tool input.
- **NDJSON adapter.** Ollama (`backends/ollama.py:67`) **explicitly sets**
  `"stream": False` (L94) and `json.loads()` of `message.content` (L106-121).
  Streaming flips that flag and assembles the NDJSON chunk sequence.
- **Orchestrator retry.** `run()` (`runtime/orchestrator.py:260`) calls `extract`
  (L293-300), validates, and on `ValidationError` does **one** corrective re-extract
  (L302-320) before `ExtractionFailed`. Validation needs the whole JSON, so it runs
  on the **assembled terminal payload**, not mid-stream.
- **Result cache.** `_RESULT_CACHE` lookup at `orchestrator.py:277`, populate at
  `:323` (only on successful validation). A half-consumed stream is uncacheable —
  cache must key on the completed terminal model.
- **MCP surface.** The `extract` tool closure `_tool()` (`mcp/app.py:129-151`) wraps
  `orchestrator.run()` (L133) under a `MAX_TEXT_BYTES` guard (L130-131) and a
  `CapturingHook` usage drain (L132-146). **No `prompiler extract` CLI** exists
  (extract deferred per RULES §9) — no CLI surface to thread.

### Track G — Streaming core (LARGE, single-owner)

- [x] **G1. Streaming contract on `BackendAdapter`.** Add an async-iterator
  streaming method (e.g. `stream_extract(...) -> AsyncIterator[StreamEvent]`) to the
  Protocol (`backends/base.py:84-102`) plus a `supports("streaming")` flag
  (mirrors L104-118). Keep `extract` as the terminal/buffered path. *Exit:* the
  Protocol defines the streaming signature; `supports("streaming")` is queryable; a
  non-streaming adapter cleanly reports unsupported; mypy strict clean. **Cite the
  affected FRs (PRD §6) for the contract change (§7.1).**
- [x] **G2. SSE adapters — Claude / OpenAI / Gemini.** Implement SSE delta parsing
  for the three backends (`claude.py:115`, `openai.py:109`, `gemini.py:125`),
  assembling the forced tool-use / function-call input incrementally. *Exit:* each
  adapter's streaming test replays an SSE cassette and yields incremental events
  whose assembled terminal dict is field-equal to the non-streaming path's result.
- [x] **G3. NDJSON adapter — Ollama.** Flip the hardcoded `"stream": False`
  (`ollama.py:94`) to a streaming path that consumes the `/api/chat` NDJSON chunk
  sequence and assembles `message.content`. *Exit:* the streaming Ollama test
  replays an NDJSON cassette; the assembled terminal dict equals the non-streaming
  result.
- [x] **G4. Orchestrator streaming entrypoint + retry parity.** Add a streaming
  path to `runtime/orchestrator.py` (`run()` L260; retry L302-320) that yields
  partials but runs Pydantic validation + the single corrective re-extract on the
  **assembled terminal payload**. *Exit:* a streaming run whose terminal payload
  validates passes through; one whose terminal payload fails validation retries once
  then raises `ExtractionFailed`, matching non-streaming semantics exactly.

### Track H — Surface integration + cache/fallback (MEDIUM)

- [x] **H1. Result-cache interaction.** Populate `_RESULT_CACHE` (`orchestrator.py`
  lookup L277, store L323) **only** with the terminal validated model after the
  stream drains; a cache hit replays as an already-complete (non-streaming) result.
  *Exit:* a streamed extract populates the cache only on completion; an immediate
  re-run hits cache and returns the full model without re-streaming; an aborted
  stream leaves no cache entry.
- [x] **H2. MCP streaming surface.** Thread streaming through the MCP `extract`
  tool (`mcp/app.py:118-164`), preserving the `MAX_TEXT_BYTES` guard (L130-131) and
  `CapturingHook` usage drain (L132-146). *Exit:* an MCP integration test runs a
  streaming extract; usage `_meta` still drains; oversized input is still rejected
  pre-call.
- [x] **H3. Opt-out + non-streaming fallback.** A disable switch forces the buffered
  `extract` path; an adapter lacking `supports("streaming")` transparently buffers
  when routed through the streaming entrypoint. *Exit:* the disable flag forces
  buffered extract (no stream consumed); a non-streaming adapter routed through the
  streaming path transparently falls back; no silent capability drop.

### Q4 exit criteria (phase-done, feeds §7 gate)

- [x] All G* and H* boxes checked; full suite green; coverage ≥ 80%.
- [x] mypy strict clean across touched modules.
- [x] **§7.1 contract narrowing cited:** the `extract`→async-iterator streaming
  surface on `BackendAdapter` and all four adapters lists the affected FRs (PRD §6)
  in the phase PR description.
- [x] Streaming and buffered paths return **field-equal terminal payloads**
  (parity); the determinism contract (`seed`, `system_fingerprint`, `deterministic`
  on `ExtractResult`) is preserved on the assembled result.
- [x] No prompt/response payloads logged below `trace` (RULES.md §8) — stream chunks
  are payloads; audit the new SSE/NDJSON parsers for leakage.
- [x] Cache stores only completed streams; an aborted/partial stream leaves no entry
  (H1); any streaming cassette wire bodies are redacted before commit (RULES §8).

---

## 2.5 Q5 — Hardening, docs, release (expanded)

**Phase tag base for §6 gate:** `main` @ `bd84eca` (Q4 close — single-`v0.2.0`
policy means Q4 ships no tag, so the gate base is the Q4-close commit, not a tag).
The pre-Q5 streaming↔buffered parity fixes this gate surfaced (PR #53,
`c8b6828`) are already merged; Q5 branches from `c8b6828`.
**Blast radius:** LOW — Q5 is mostly non-code (docs refresh + release prep) plus a
hardening audit. The only code touches are the two-file version bump and any
audit-driven fixes; docs are isolated; no contract narrows. PRD anchors: §3
(containerized release goal), §7 NFRs (perf/reliability/security/compat),
FR-13/14/15, §8.2 (v2 accepted set).

**Scope grounding (from codebase):**

- **Release infra already exists from v1.** `.github/workflows/release.yml`
  (verify-tag → build-dist → publish-pypi via OIDC → publish-image to
  `ghcr.io/iversatile/prompiler` → github-release) and the `Dockerfile` are fully
  built. Q5 does **not** rebuild them — it triggers them via the version bump and
  the §5 user-approved `v0.2.0` tag.
- **Version single source of truth.** `pyproject.toml` `version = "0.1.3"` and
  `src/prompiler/__init__.py` `__version__ = "0.1.3"`; `release.yml` verify-tag
  asserts the `v*` tag equals `prompiler.__version__`. Both must bump to `0.2.0`
  in lockstep.
- **User-facing docs are v1-only.** `README.md`, `docs/CLI.md`, `docs/API.md` do
  not cover multimodal, `--auto-apply`, streaming, the compile/result caches,
  `prompiler login` (keychain/OAuth), `migrate-spec`, or spec composition.
  `docs/TUTORIAL.md:27` still shows `spec_version: 1`, which is **invalid** after
  Q3 bumped specs to version 2. `docs/architecture.md` frames v2 features as "out
  of scope here"/future (e.g. L216, L305) rather than shipped.
- **PRD §8→§6 promotion** (PLAN §5): the six accepted §8 Out-of-Scope items are
  promoted into PRD §6 In-Scope — resolved in J3.
- **Gate evidence sources.** The FR traceability matrix
  (`tests/test_fr_traceability.py`, FR-1..FR-14; FR-15 container excluded from the
  functional matrix) and the PRD §7.1 perf budgets are what the §7 Phase Done Gate
  reads.

### Track I — Hardening (LOW–MEDIUM, audit-driven)

- [x] **I1. Security + secret audit.** Run the two-layer secret scan
  (`scripts/scan_secrets.py` + gitleaks) and `bandit -r src/` across the tree, and
  audit the new v2 code paths — modal bytes, stream chunks, credential
  read/refresh, auto-apply spec writes — for payload leakage below `trace` and
  credential leakage to stdout/stderr (RULES §8). *Exit:* secret scan + bandit
  report clean; a short audit note confirms no payload/credential leakage in the
  v2 surface; any fix carries an `Applying LL-NNN` cite.
- [x] **I2. Perf-budget verification.** Confirm PRD §7.1 budgets still hold after
  the v2 additions (cache, streaming, composition): compile <200ms, validate
  <50ms, run overhead <50ms, `run_batch` 100@conc=8 <60s, MCP cold start <1s.
  *Exit:* a perf check (script/test) records each budget as met; any regression is
  fixed or recorded with rationale (perf-timing is the §7.1 manual-testing
  carve-out).
- [x] **I3. Coverage + type-strictness sweep.** Full suite green; coverage ≥ 80%
  repo-wide; mypy strict clean across the **whole** package (not just touched
  modules). *Exit:* `pytest --cov` ≥ 80%; mypy strict clean; FR traceability
  matrix green (`tests/test_fr_traceability.py`).

### Track J — Docs refresh to v2 (LOW, isolated)

- [x] **J1. README + TUTORIAL.** Refresh `README.md` to the shipped v2 surface
  (multimodal, `--auto-apply`, streaming, caches, `login`, `migrate-spec`,
  composition); fix `docs/TUTORIAL.md:27` `spec_version: 1`→`2` and any other
  v1-only examples. *Exit:* README lists every v2 capability; TUTORIAL examples
  load under `spec_version: 2`; no invalid-version example remains.
- [x] **J2. CLI + API reference.** Update `docs/CLI.md` (`login`, `migrate-spec`,
  `refine --auto-apply`, streaming/cache flags) and `docs/API.md`
  (`KeychainProvider`/`OAuthProvider`, the streaming surface, cache opt-out) to the
  v2 public surface. *Exit:* every v2 CLI command + flag and public API symbol is
  documented; no documented command/flag is absent from the code.
- [x] **J3. architecture.md + PRD scope.** Update `docs/architecture.md` from "v2
  out of scope here"/future framing (L216, L305) to shipped reality (keychain/OAuth,
  `--auto-apply`, streaming, composition, the two cache layers). Complete the
  deferred Q0 PRD task: promote the six accepted §8 Out-of-Scope items into PRD §6
  In-Scope (PLAN §5 L469). *Exit:* architecture.md describes the v2 design as
  shipped; PRD §6 lists the six promoted items; the PLAN §5 promotion note is
  resolved.

### Track K — Release prep (LOW, gated)

- [x] **K1. Version bump.** Bump `pyproject.toml` `0.1.3`→`0.2.0` and
  `src/prompiler/__init__.py` `__version__` in lockstep. *Exit:* both read
  `0.2.0`; the `release.yml` verify-tag invariant (tag == `__version__`) would hold
  for a `v0.2.0` tag; `uv build` produces sdist+wheel and `twine check` is clean in
  a dry run.
- [x] **K2. Local release gate + golden + container dry-run.** Run the §9 local
  gate, verify/regenerate the V2_VALIDATION golden snapshots
  (`tests/test_e2e_clients.py`), and a CPU-only container build dry-run. *Exit:* §9
  gate passes; golden snapshots stable (no unexpected drift); the container image
  builds and runs CPU-only.
- [ ] **K3. Changelog + release notes.** Curate the `git log <last-tag>..HEAD`
  history into v0.2.0 release notes covering the v2 headline capabilities. *Exit:*
  changelog/release notes drafted and committed, ready to feed the §5 Version Tag
  Gate. **The actual `v0.2.0` tag is the §5 user-approved gate — never
  self-applied.**

### Q5 exit criteria (phase-done, feeds §7 gate)

- [ ] All I*, J*, and K* boxes checked; full suite green; coverage ≥ 80% repo-wide.
- [ ] mypy strict clean across the whole package; FR traceability matrix green
  (FR-1..FR-14; FR-15 container excluded from the functional matrix).
- [ ] Security: two-layer secret scan + bandit clean; a documented audit confirms
  no payload/credential leakage in the v2 surface (RULES §8).
- [ ] Docs cover the full v2 surface; no invalid `spec_version: 1` example remains;
  PRD §6 reflects the promoted v2 scope.
- [ ] Version is `0.2.0` in lockstep (pyproject + `__init__`); the release dry-run
  (build + container) passes; changelog/release notes are ready.
- [ ] §5 Version Tag Gate is the sole path to the single `v0.2.0` tag — explicit
  user approval required, never self-approved.

---

## 3. Open questions

1. ~~v2 theme: depth vs breadth?~~ **RESOLVED: Mixed** (§1).
2. ~~#8 — recover or drop?~~ **RESOLVED** (§0): recovered as "LL-NNN for
   CI-exemption fix"; **DONE — written as LL-009** (LL-008 was the determinism entry).
3. ~~Backwards-compat contract?~~ **RESOLVED: bump to `spec_version: 2`** — no
   external consumers yet, migration cost ≈ 0. **Clean break, no dual-path
   loader:** `version: 1` errors out with a pointer to a one-shot `prompiler
   migrate-spec`; only one version live at a time. Repo examples/fixtures
   migrate in the Q3 PR. Sub-qs RESOLVED at Q3 start (see 2.3):
   - spec_hash folding — RESOLVED: YES. Hash over the
     fully-resolved/flattened form so cache keys stay correct when a parent
     changes. spec_hash already runs on the loader's output; flatten-before-walk
     makes that output the flattened spec, so folding falls out for free with no
     hash-call relocation. Locked as Q3 task E4.
   - Field provenance — RESOLVED: DEFER. Flattening collapses inheritance
     before walk.py sees the spec, which loses the record of which parent each
     field came from. No Q3 consumer needs that origin yet, so the flattener does
     NOT attach provenance metadata in Q3. Trigger to populate: the first
     consumer that needs field origin — parent-aware error messages, or diff/debug
     tooling. Cheap to add later (origin spec id per field on the flattened
     EntitySpec); revisit when that consumer lands, not before.
4. ~~Release cadence?~~ **RESOLVED: Single `v0.2.0`** (§2).
5. ~~codegen-IR trigger?~~ **RESOLVED: stays un-fired, by design.** Q3
   composition uses **flatten-before-walk** (§2 Q3) — the loader resolves
   inheritance into one flat EntitySpec *before* `walk.py` sees it, so the walk
   keeps its existing flat-spec contract and grows no new emitter. The codegen-IR
   decision only goes live if a later phase makes the walk itself
   inheritance-aware or adds a genuine 3rd renderer.

---

## 4. Cross-cutting (unchanged from v1)

Security review, perf budgets, secret-handling, CPU-only constraint, and the
RULES.md gates carry forward verbatim. See `PLAN.BK.MD` §4 and `docs/RULES.md`.

---

## 5. PRD cross-check (v2 scope provenance)

Every accepted §1 feature traces to an existing PRD line — v2 scope is a
promotion of already-documented non-goals, not net-new invention:

| §1 feature | PRD anchor |
|------------|-----------|
| Multi-modal input | §8 Out of Scope; §3 v1 non-goals |
| Auto-apply refinement | §8; **FR-7** ("v2: `--auto-apply` re-runs eval until metric threshold or N iterations") |
| Compile-result cache | §8 ("deferred to v2"); **FR-14** ("v2: hash-keyed cache `(spec_hash, backend, model, input_hash) → result`") |
| Keychain / OAuth | §8; **FR-10** ("v2: `KeychainProvider`, `OAuthProvider`") |
| Spec composition | §8; §3 (removes "single flat spec" v1 limit) |
| Streaming | §8; §3 v1 non-goals |

Not promoted: **Hosted SaaS UI** (§8 — out of OSS core) and **§12 Code of
Conduct** (governance only, zero runtime impact). Resolved in J3: the six
accepted items were promoted from §8 Out-of-Scope into the §6 functional
requirements (shipped framing), and PRD §8.2 now reads "in scope, implemented".

---

## Appendix — v1 reference

Full v1 plan (phases P0–P8, test strategy, risks, v1 DoD) is preserved in
`docs/PLAN.BK.MD`. v1 shipped as `v0.1.1` (PyPI + GitHub release + demo). Do not
re-litigate closed v1 phases here.
