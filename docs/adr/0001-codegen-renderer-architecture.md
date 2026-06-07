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
