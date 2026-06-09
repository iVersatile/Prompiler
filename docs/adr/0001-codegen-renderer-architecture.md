# ADR 0001 — Codegen renderer architecture: hold at shared FieldVisitor

- **Status:** Accepted
- **Date:** 2026-06-08
- **Phase:** P8 (Hardening, Docs, Release)

## Context

`docs/PLAN.md` §P8 requires an explicit decision on the codegen renderer
architecture: deepen the shared field visitor (introduced in P1.x) into a full
`FieldSpec` IR module (~1.5d), stop at `FieldSpec`-attached helpers (~<1d), or
hold the current shape as-is.

The shared traversal lives in `src/prompiler/compiler/walk.py`:

- `FieldVisitor[T]` — a `runtime_checkable` `Protocol` with one method per field
  shape (`visit_scalar`, `visit_enum`, `visit_array`, `visit_object`).
- `walk_field` / `walk_fields` — own recursion, sub-class naming
  (`pascal_case`), and child pre-computation, so a visitor only describes the
  per-shape mapping.

Exactly two renderers consume this layer today:

| Consumer | Emits |
|----------|-------|
| `src/prompiler/compiler/model.py` | runtime Pydantic v2 model |
| `src/prompiler/codegen.py` | backend tool-schemas |

Sharing recursion shape between them is the whole point of the visitor: codegen
and the runtime model cannot drift on traversal order or sub-class naming
because there is one walker.

## Decision

**Hold as-is.** Keep the shared `FieldVisitor` + `walk_*` functions. Do **not**
introduce a separate `FieldSpec` IR module, and do **not** attach renderer
helpers onto `FieldSpec`.

The plan defined two trigger criteria for revisiting this; the decision is gated
on them:

1. **A third renderer arrives** (e.g., a TypeScript emitter or a JSON Schema
   dialect variant). Two consumers do not justify an IR layer — that is
   speculative generality (YAGNI). A third concrete consumer is the signal that
   the abstraction is load-bearing.
2. **A codegen-vs-runtime drift incident is recorded** in
   `docs/LESSONS_LEARNT.md`. If the single-walker guarantee ever fails to
   prevent drift in practice, that is evidence the shape is insufficient.

Neither trigger has fired as of this ADR:

- Renderer count is two (`model.py`, `codegen.py`), verified by grepping
  `walk_field` / `walk_fields` / `FieldVisitor` consumers in `src/prompiler`.
- No drift lesson (`LL-NNN`) exists in `docs/LESSONS_LEARNT.md`.

## Consequences

- **Positive:** No speculative IR module to maintain. The visitor stays small
  (~50 lines) and the drift-prevention invariant is preserved by construction.
- **Positive:** A future contributor adding a third renderer has a clear,
  recorded trigger and a bounded estimate (~1.5d for the IR path, ~<1d for the
  helper path) rather than an open-ended refactor.
- **Negative / accepted:** Adding the *third* renderer will pay a one-time tax
  to either thread a third visitor through `walk.py` as-is or perform the
  deferred IR extraction at that point. This is the intended trade: defer the
  cost until a real second data point exists.

## Revisit when

Re-open this ADR (supersede with ADR 0002) the moment either trigger fires:
a third renderer lands, or a drift incident is logged as a numbered lesson in
`docs/LESSONS_LEARNT.md`.

## v2 watch (armed)

This decision carries unchanged into v2 (`v0.2.0`). The two triggers above stay
armed across all v2 phases — check them at each phase boundary:

- **Q3 (spec composition):** stays **un-fired by design**. Composition uses
  flatten-before-walk — the loader resolves inheritance into one flat
  `EntitySpec` *before* `walk.py` sees it, so the walk keeps its existing
  flat-spec contract and grows no new emitter (`docs/PLAN.md` §3 Q5).
- A new trigger condition is added for v2: the watch **also** fires if any phase
  makes the walk itself **inheritance-aware** (i.e. `walk.py` stops assuming a
  flat spec), not only when a 3rd renderer lands.
- Renderer count remains two (`model.py`, `codegen.py`) entering v2.
