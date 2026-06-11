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

Q5  Hardening, docs, release → v0.2.0
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

- [ ] All C* and D* boxes checked; full suite green; coverage ≥ 80%.
- [ ] mypy strict clean across touched modules.
- [ ] No credentials/tokens in stdout/stderr or logged below `trace` (RULES.md §8)
  — audit `login`, keychain read, and OAuth refresh paths.
- [ ] `--auto-apply` writes are git-tracked and reversible; no silent overwrite on
  a dirty tree.

---

## 3. Open questions

1. ~~v2 theme: depth vs breadth?~~ **RESOLVED: Mixed** (§1).
2. ~~#8 — recover or drop?~~ **RESOLVED** (§0): recovered as "LL-NNN for
   CI-exemption fix"; **DONE — written as LL-009** (LL-008 was the determinism entry).
3. ~~Backwards-compat contract?~~ **RESOLVED: bump to `spec_version: 2`** — no
   external consumers yet, migration cost ≈ 0. **Clean break, no dual-path
   loader:** `version: 1` errors out with a pointer to a one-shot `prompiler
   migrate-spec`; only one version live at a time. Repo examples/fixtures
   migrate in the Q3 PR. Sub-qs deferred to Q3 start:
   - **`spec_hash` folding** (recommend **yes**) — hash over the
     fully-resolved/flattened form so cache keys stay correct when a parent
     changes.
   - **Field provenance (caveat on flatten-before-walk):** flattening collapses
     inheritance before `walk.py` sees the spec, which *loses* the record of
     which parent each field came from. If any feature needs that origin (e.g.
     error messages that point back to the defining parent spec, or
     debugging/diff tooling), the flattener **must attach provenance metadata**
     (origin spec id per field) onto the flattened EntitySpec. Cheap to carry;
     decide in Q3 whether to populate it now or defer until a consumer needs it.
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
Conduct** (governance only, zero runtime impact). Next PRD edit: promote the six
accepted items from §8 Out-of-Scope into §6 In-Scope (a Q0 task).

---

## Appendix — v1 reference

Full v1 plan (phases P0–P8, test strategy, risks, v1 DoD) is preserved in
`docs/PLAN.BK.MD`. v1 shipped as `v0.1.1` (PyPI + GitHub release + demo). Do not
re-litigate closed v1 phases here.
